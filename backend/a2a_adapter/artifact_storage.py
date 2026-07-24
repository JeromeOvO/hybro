"""Materialize A2A file parts into durable room files."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp.abc import AbstractResolver

from common.types import DataPart, FileContent, Part

MAX_FILE_PARTS = 20
MAX_FILE_RAW_BYTES = 50 * 1024 * 1024
MAX_TOTAL_RAW_BYTES = 100 * 1024 * 1024
MAX_TOTAL_ENCODED_BYTES = 139_810_136
MAX_REDIRECTS = 3

_room_files: Any | None = None


def bind_artifact_files(room_files: Any) -> None:
    global _room_files

    _room_files = room_files


def _require_room_files() -> Any:
    if _room_files is None:
        raise RuntimeError("Artifact room files dependency has not been bound")
    return _room_files


def _room_file_content_url(file_id: str) -> str:
    storage = _require_room_files()
    content_url = getattr(storage, "content_url", None)
    if callable(content_url):
        return str(content_url(file_id))
    return f"/api/v1/files/{file_id}/content"


def _declared_mime(file_content: Any) -> str | None:
    return getattr(file_content, "mime_type", None) or getattr(
        file_content, "mimeType", None
    )


def _mime(file_content: Any) -> str:
    return _declared_mime(file_content) or "application/octet-stream"


def _origin_key(
    *,
    room_id: str,
    message_id: str,
    artifact_slot: str,
    part_slot: int,
    content_sha256: str,
) -> str:
    payload = [
        "v1",
        room_id,
        message_id,
        artifact_slot,
        part_slot,
        content_sha256,
    ]
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _decode_base64(value: str) -> bytes:
    if len(value) > 4 * ((MAX_FILE_RAW_BYTES + 2) // 3):
        raise ValueError("encoded file exceeds per-file limit")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 file content") from exc
    if len(decoded) > MAX_FILE_RAW_BYTES:
        raise ValueError("decoded file exceeds per-file limit")
    return decoded


def _unavailable_dict(file_info: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "kind": "data",
        "data": {
            "type": "file_unavailable",
            "file_name": file_info.get("name") or "file",
            "mime_type": (
                file_info.get("mime_type")
                or file_info.get("mimeType")
                or "application/octet-stream"
            ),
            "reason": reason,
        },
    }


def _unavailable_part(file_content: Any, reason: str) -> Part:
    return Part(
        root=DataPart(
            data={
                "type": "file_unavailable",
                "file_name": getattr(file_content, "name", None) or "file",
                "mime_type": _mime(file_content),
                "reason": reason,
            }
        )
    )


def _authoritative_size(record: dict[str, Any]) -> int:
    try:
        size = int(record["size_bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid stored file size") from exc
    if size < 0:
        raise ValueError("invalid stored file size")
    return size


def _declared_metadata_size(metadata: dict[str, Any]) -> int:
    try:
        size = int(metadata.get("size_bytes") or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, size)


def _canonical_reference_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": str(record["file_id"]),
        "file_name": str(record.get("file_name") or "file"),
        "mime_type": str(record.get("mime_type") or "application/octet-stream"),
        "size_bytes": _authoritative_size(record),
        "sha256": str(record["sha256"]),
    }


def _canonicalize_reference_dict(
    part: dict[str, Any],
    file_info: dict[str, Any],
    record: dict[str, Any],
) -> None:
    metadata = _canonical_reference_metadata(record)
    file_info.clear()
    file_info.update(
        {
            "uri": _room_file_content_url(metadata["file_id"]),
            "mimeType": metadata["mime_type"],
            "name": metadata["file_name"],
        }
    )
    part["metadata"] = metadata


def _claim_file_attempt(budget: dict[str, Any]) -> bool:
    attempted = int(budget.get("attempted", budget.get("converted", 0)))
    if attempted >= MAX_FILE_PARTS:
        return False
    budget["attempted"] = attempted + 1
    return True


async def _store(
    *,
    data: bytes,
    room_id: str,
    message_id: str,
    artifact_slot: str,
    part_slot: int,
    file_name: str,
    mime_type: str,
) -> dict[str, Any]:
    digest = hashlib.sha256(data).hexdigest()
    return await _require_room_files().store_agent_artifact(
        room_id=room_id,
        source_message_id=message_id,
        origin_key=_origin_key(
            room_id=room_id,
            message_id=message_id,
            artifact_slot=artifact_slot,
            part_slot=part_slot,
            content_sha256=digest,
        ),
        content=data,
        file_name=file_name,
        mime_type=mime_type,
        max_bytes=MAX_FILE_RAW_BYTES,
    )


async def _consume_precounted_reference(
    *,
    part: dict[str, Any],
    file_info: dict[str, Any],
    room_id: str,
    message_id: str,
    budget: dict[str, Any],
) -> bool:
    metadata = part.get("metadata") or {}
    file_id = str(metadata.get("file_id") or "")
    precounted = budget.get("precounted_file_ids")
    if not isinstance(precounted, dict):
        return False
    remaining = int(precounted.get(file_id) or 0)
    if remaining <= 0:
        return False
    uri = file_info.get("uri")
    if uri != _room_file_content_url(file_id):
        return False
    valid = await _require_room_files().validate_agent_reference(
        room_id=room_id,
        source_message_id=message_id,
        file_id=file_id,
        sha256=metadata.get("sha256"),
    )
    if not valid:
        raise ValueError("untrusted internal file reference")
    authoritative_size = _authoritative_size(valid)
    budget["raw"] = (
        max(0, int(budget["raw"]) - _declared_metadata_size(metadata))
        + authoritative_size
    )
    if budget["raw"] > MAX_TOTAL_RAW_BYTES:
        raise ValueError("aggregate raw limit exceeded")
    _canonicalize_reference_dict(part, file_info, valid)
    if remaining == 1:
        precounted.pop(file_id, None)
    else:
        precounted[file_id] = remaining - 1
    return True


async def materialize_inline_file_parts(
    parts: list[dict],
    room_id: str,
    message_id: str,
    *,
    converted_so_far: int = 0,
    budget: dict[str, Any] | None = None,
    artifact_slot: str | None = None,
) -> int:
    budget = (
        budget
        if budget is not None
        else {
            "converted": converted_so_far,
            "attempted": converted_so_far,
            "raw": 0,
            "encoded": 0,
        }
    )
    budget.setdefault("attempted", int(budget.get("converted", 0)))

    for part_slot, part in enumerate(parts):
        if part.get("kind") != "file":
            continue
        file_info = part.get("file")
        if isinstance(file_info, dict):
            try:
                if await _consume_precounted_reference(
                    part=part,
                    file_info=file_info,
                    room_id=room_id,
                    message_id=message_id,
                    budget=budget,
                ):
                    continue
            except Exception as exc:
                reason = "size_limit" if "limit" in str(exc) else "invalid_content"
                parts[part_slot] = _unavailable_dict(file_info, reason)
                continue
        if not _claim_file_attempt(budget):
            parts[part_slot] = _unavailable_dict(
                file_info if isinstance(file_info, dict) else {},
                "size_limit",
            )
            continue
        if not isinstance(file_info, dict):
            parts[part_slot] = _unavailable_dict({}, "invalid_content")
            continue
        try:
            encoded = file_info.get("bytes")
            if isinstance(encoded, str) and encoded:
                budget["encoded"] += len(encoded)
                if budget["encoded"] > MAX_TOTAL_ENCODED_BYTES:
                    raise ValueError("aggregate encoded limit exceeded")
                data = _decode_base64(encoded)
            else:
                uri = file_info.get("uri")
                if not isinstance(uri, str) or not uri:
                    raise ValueError("missing file content")
                room_files = _require_room_files()
                if uri == _room_file_content_url(
                    str((part.get("metadata") or {}).get("file_id") or "")
                ):
                    metadata = part.get("metadata") or {}
                    file_id = str(metadata.get("file_id") or "")
                    valid = await room_files.validate_agent_reference(
                        room_id=room_id,
                        source_message_id=message_id,
                        file_id=file_id,
                        sha256=metadata.get("sha256"),
                    )
                    if valid:
                        authoritative_size = _authoritative_size(valid)
                        budget["converted"] += 1
                        budget["raw"] += authoritative_size
                        if budget["raw"] > MAX_TOTAL_RAW_BYTES:
                            raise ValueError("aggregate raw limit exceeded")
                        _canonicalize_reference_dict(part, file_info, valid)
                        continue
                    raise ValueError("untrusted internal file reference")
                data, detected_mime = await fetch_remote_file(uri)
                file_info.setdefault("mimeType", detected_mime)
            budget["raw"] += len(data)
            if budget["raw"] > MAX_TOTAL_RAW_BYTES:
                raise ValueError("aggregate raw limit exceeded")
            stored = await _store(
                data=data,
                room_id=room_id,
                message_id=message_id,
                artifact_slot=artifact_slot or f"message:{budget['converted']}",
                part_slot=part_slot,
                file_name=file_info.get("name") or f"artifact-{budget['converted']}",
                mime_type=(
                    file_info.get("mime_type")
                    or file_info.get("mimeType")
                    or "application/octet-stream"
                ),
            )
        except Exception as exc:
            reason = "size_limit" if "limit" in str(exc) else "invalid_content"
            parts[part_slot] = _unavailable_dict(file_info, reason)
            continue

        file_id = str(stored["file_id"])
        file_info.clear()
        file_info.update(
            {
                "uri": _room_file_content_url(file_id),
                "mimeType": stored["mime_type"],
                "name": stored["file_name"],
            }
        )
        part["metadata"] = {
            "file_id": file_id,
            "file_name": stored["file_name"],
            "mime_type": stored["mime_type"],
            "size_bytes": stored["size_bytes"],
            "sha256": stored["sha256"],
        }
        budget["converted"] += 1
    return budget["converted"]


async def delete_superseded_agent_artifacts(
    *,
    room_id: str,
    message_id: str,
    file_ids: set[str],
) -> int:
    return await _require_room_files().delete_superseded_agent_artifacts(
        room_id=room_id,
        source_message_id=message_id,
        file_ids=file_ids,
    )


async def materialize_artifacts(
    artifacts: list,
    room_id: str,
    message_id: str,
    *,
    converted_so_far: int = 0,
) -> int:
    converted = converted_so_far
    attempted = converted_so_far
    total_raw = 0
    total_encoded = 0

    for artifact_position, artifact in enumerate(artifacts):
        artifact_id = getattr(artifact, "artifact_id", None)
        explicit_index = getattr(artifact, "index", None)
        artifact_slot = (
            f"id:{artifact_id}"
            if artifact_id
            else f"index:{explicit_index}"
            if explicit_index is not None
            else f"slot:{artifact_position}"
        )
        for part_slot, part in enumerate(list(getattr(artifact, "parts", []) or [])):
            root = getattr(part, "root", part)
            if getattr(root, "kind", None) != "file":
                continue
            file_content = getattr(root, "file", None)
            if attempted >= MAX_FILE_PARTS:
                artifact.parts[part_slot] = _unavailable_part(
                    file_content, "size_limit"
                )
                continue
            attempted += 1
            if file_content is None:
                artifact.parts[part_slot] = _unavailable_part(
                    file_content, "invalid_content"
                )
                continue
            try:
                encoded = getattr(file_content, "bytes", None)
                if encoded:
                    total_encoded += len(encoded)
                    if total_encoded > MAX_TOTAL_ENCODED_BYTES:
                        raise ValueError("aggregate encoded limit exceeded")
                    data = _decode_base64(encoded)
                else:
                    uri = getattr(file_content, "uri", None)
                    if not uri:
                        raise ValueError("missing file content")
                    room_files = _require_room_files()
                    metadata = getattr(root, "metadata", None) or {}
                    file_id = str(metadata.get("file_id") or "")
                    if str(uri) == _room_file_content_url(file_id):
                        valid = await room_files.validate_agent_reference(
                            room_id=room_id,
                            source_message_id=message_id,
                            file_id=file_id,
                            sha256=metadata.get("sha256"),
                        )
                        if valid:
                            total_raw += _authoritative_size(valid)
                            if total_raw > MAX_TOTAL_RAW_BYTES:
                                raise ValueError("aggregate raw limit exceeded")
                            canonical = _canonical_reference_metadata(valid)
                            root.file = FileContent(
                                uri=_room_file_content_url(canonical["file_id"]),
                                mimeType=canonical["mime_type"],
                                name=canonical["file_name"],
                            )
                            root.metadata = canonical
                            converted += 1
                            continue
                        raise ValueError("untrusted internal file reference")
                    data, detected_mime = await fetch_remote_file(str(uri))
                    if not _declared_mime(file_content):
                        file_content.mime_type = detected_mime
                total_raw += len(data)
                if total_raw > MAX_TOTAL_RAW_BYTES:
                    raise ValueError("aggregate raw limit exceeded")
                stored = await _store(
                    data=data,
                    room_id=room_id,
                    message_id=message_id,
                    artifact_slot=artifact_slot,
                    part_slot=part_slot,
                    file_name=getattr(file_content, "name", None)
                    or f"artifact-{artifact_position}-{part_slot}",
                    mime_type=_mime(file_content),
                )
            except Exception as exc:
                reason = "size_limit" if "limit" in str(exc) else "invalid_content"
                artifact.parts[part_slot] = _unavailable_part(file_content, reason)
                continue

            file_id = str(stored["file_id"])
            root.file = FileContent(
                uri=_room_file_content_url(file_id),
                mimeType=stored["mime_type"],
                name=stored["file_name"],
            )
            root.metadata = {
                "file_id": file_id,
                "file_name": stored["file_name"],
                "mime_type": stored["mime_type"],
                "size_bytes": stored["size_bytes"],
                "sha256": stored["sha256"],
            }
            converted += 1
    return converted


class _PinnedResolver(AbstractResolver):
    def __init__(self, hostname: str, addresses: list[tuple[int, str]]) -> None:
        self._hostname = hostname
        self._addresses = addresses

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[dict[str, Any]]:
        if host != self._hostname:
            raise OSError("unexpected hostname")
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": address_family,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
            for address_family, address in self._addresses
        ]

    async def close(self) -> None:
        return None


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


async def _resolve_public(hostname: str, port: int) -> list[tuple[int, str]]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        hostname,
        port,
        type=socket.SOCK_STREAM,
    )
    addresses: list[tuple[int, str]] = []
    for family, _type, _proto, _canonname, sockaddr in infos:
        address = sockaddr[0]
        if not _public_address(address):
            raise ValueError("remote URI resolves to a non-public address")
        pair = (family, address)
        if pair not in addresses:
            addresses.append(pair)
    if not addresses:
        raise ValueError("remote URI has no usable address")
    return addresses


async def fetch_remote_file(uri: str) -> tuple[bytes, str]:
    current = uri
    timeout = aiohttp.ClientTimeout(total=60, connect=5, sock_read=30)
    for redirect_count in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("unsupported remote URI")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = await _resolve_public(parsed.hostname, port)
        connector = aiohttp.TCPConnector(
            resolver=_PinnedResolver(parsed.hostname, addresses),
            use_dns_cache=False,
        )
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
        ) as session:
            async with session.get(current, allow_redirects=False) as response:
                connection = response.connection
                transport = connection.transport if connection is not None else None
                peer = transport.get_extra_info("peername") if transport else None
                peer_address = peer[0] if isinstance(peer, tuple) and peer else None
                if (
                    not isinstance(peer_address, str)
                    or not _public_address(peer_address)
                    or peer_address not in {address for _, address in addresses}
                ):
                    raise ValueError("remote URI peer did not match pinned DNS")
                if response.status in {301, 302, 303, 307, 308}:
                    if redirect_count >= MAX_REDIRECTS:
                        raise ValueError("too many redirects")
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redirect missing location")
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    raise ValueError(f"remote URI returned {response.status}")
                declared = response.content_length
                if declared is not None and declared > MAX_FILE_RAW_BYTES:
                    raise ValueError("remote file exceeds size limit")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > MAX_FILE_RAW_BYTES:
                        raise ValueError("remote file exceeds size limit")
                    chunks.append(chunk)
                mime_type = (
                    response.headers.get("content-type", "application/octet-stream")
                    .split(";", 1)[0]
                    .strip()
                    or "application/octet-stream"
                )
                return b"".join(chunks), mime_type
    raise ValueError("remote URI could not be fetched")


__all__ = [
    "bind_artifact_files",
    "delete_superseded_agent_artifacts",
    "fetch_remote_file",
    "materialize_artifacts",
    "materialize_inline_file_parts",
]
