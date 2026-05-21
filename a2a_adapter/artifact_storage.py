"""A2A artifact storage conversion helpers.

This module owns the storage-aware conversion of inline A2A file bytes and
external file URIs into durable object-storage references.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from a2a.types import FileWithUri

from common.file_upload_constants import MAX_INLINE_CONVERSIONS_PER_MESSAGE

logger = logging.getLogger(__name__)

_storage_service: Any | None = None
_own_bucket_name = ""
_max_download_bytes = 50 * 1024 * 1024


def bind_a2a_storage_dependencies(
    *,
    storage_service: Any,
    s3_bucket_name: str = "",
    max_file_size_mb: int = 50,
) -> None:
    global _storage_service, _own_bucket_name, _max_download_bytes

    _storage_service = storage_service
    _own_bucket_name = s3_bucket_name
    _max_download_bytes = max_file_size_mb * 1024 * 1024


def _require_storage_service() -> Any:
    if _storage_service is None:
        raise RuntimeError("A2A artifact storage dependency has not been bound")
    return _storage_service


def _is_own_storage_url(uri: str) -> bool:
    if not _own_bucket_name:
        return False
    parsed = urlparse(uri)
    host = parsed.hostname or ""
    return _own_bucket_name in host


def _validate_external_uri(uri: str) -> str | None:
    parsed = urlparse(uri)

    if parsed.scheme not in ("http", "https"):
        return f"unsupported scheme: {parsed.scheme}"

    hostname = parsed.hostname
    if not hostname:
        return "missing hostname"

    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return f"DNS resolution failed for {hostname}"

    for _family, _type, _proto, _canonname, sockaddr in resolved:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return f"resolved to private/reserved IP: {ip}"

    return None


async def _read_limited_response_body(
    response: httpx.Response,
    max_bytes: int,
) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _response_content_type(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "")
    return content_type.split(";", 1)[0] or "application/octet-stream"


def _response_content_length(response: httpx.Response) -> int | None:
    raw_length = response.headers.get("content-length")
    if raw_length is None:
        return None
    try:
        return int(raw_length)
    except ValueError:
        return None


async def convert_inline_bytes_to_s3(
    parts: list[dict],
    room_id: str,
    message_id: str,
    *,
    converted_so_far: int = 0,
) -> int:
    converted = converted_so_far

    for part in parts:
        if part.get("kind") != "file":
            continue
        file_info = part.get("file")
        if not file_info or not isinstance(file_info, dict):
            continue
        raw_bytes = file_info.get("bytes")
        if not raw_bytes:
            continue

        if converted >= MAX_INLINE_CONVERSIONS_PER_MESSAGE:
            logger.warning(
                "Inline conversion cap (%d) reached: room=%s message=%s - skipping remaining",
                MAX_INLINE_CONVERSIONS_PER_MESSAGE, room_id, message_id,
            )
            break

        try:
            decoded = base64.b64decode(raw_bytes)
        except Exception:
            logger.warning(
                "Invalid base64 in file part: room=%s message=%s - skipping",
                room_id, message_id,
            )
            continue

        mime = file_info.get("mime_type") or file_info.get("mimeType") or "application/octet-stream"
        ext = mime.split("/")[-1] if "/" in mime else "bin"
        storage_key = f"artifacts/{room_id}/{message_id}/notify-{converted}.{ext}"

        try:
            storage = _require_storage_service()
            await storage.upload_file(
                file_data=io.BytesIO(decoded),
                s3_key=storage_key,
                content_type=mime,
                content_length=len(decoded),
            )
            orig_name = file_info.get("name")
            presigned_url = await storage.generate_presigned_url(
                storage_key, filename=orig_name,
            )
            file_info["bytes"] = None
            file_info["uri"] = presigned_url
            if part.get("metadata") is None:
                part["metadata"] = {}
            part["metadata"]["s3_key"] = storage_key
            converted += 1
        except Exception:
            logger.error(
                "Failed to upload inline file part to storage: room=%s message=%s",
                room_id, message_id, exc_info=True,
            )

    return await _download_external_uris_to_s3(
        parts, room_id, message_id, converted_so_far=converted,
    )


async def _download_external_uris_to_s3(
    parts: list[dict],
    room_id: str,
    message_id: str,
    *,
    converted_so_far: int = 0,
) -> int:
    converted = converted_so_far
    uri_parts: list[tuple[dict, dict, str]] = []

    for part in parts:
        if part.get("kind") != "file":
            continue
        file_info = part.get("file")
        if not file_info or not isinstance(file_info, dict):
            continue
        if file_info.get("bytes"):
            continue
        uri = file_info.get("uri")
        if not uri or _is_own_storage_url(uri):
            continue
        rejection = _validate_external_uri(uri)
        if rejection:
            logger.warning(
                "Skipping unsafe external URI (%s): room=%s message=%s uri=%s",
                rejection, room_id, message_id, uri[:120],
            )
            continue
        uri_parts.append((part, file_info, uri))

    if not uri_parts:
        return converted

    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        max_redirects=3,
    ) as client:
        for part_dict, file_info, uri in uri_parts:
            if converted >= MAX_INLINE_CONVERSIONS_PER_MESSAGE:
                logger.warning(
                    "Conversion cap (%d) reached during URI download: room=%s message=%s",
                    MAX_INLINE_CONVERSIONS_PER_MESSAGE, room_id, message_id,
                )
                break

            try:
                async with client.stream("GET", uri) as response:
                    if response.status_code != 200:
                        logger.warning(
                            "External URI returned HTTP %d: room=%s message=%s uri=%s",
                            response.status_code,
                            room_id,
                            message_id,
                            uri[:120],
                        )
                        continue
                    content_length = _response_content_length(response)
                    if content_length is not None and content_length > _max_download_bytes:
                        logger.warning(
                            "External URI Content-Length %d exceeds limit %d: room=%s message=%s",
                            content_length, _max_download_bytes, room_id, message_id,
                        )
                        continue
                    data = await _read_limited_response_body(
                        response,
                        _max_download_bytes,
                    )
                    if data is None:
                        logger.warning(
                            "External URI body exceeds size limit (%d bytes): room=%s message=%s",
                            _max_download_bytes, room_id, message_id,
                        )
                        continue
                    content_type = (
                        _response_content_type(response)
                        or file_info.get("mime_type")
                        or file_info.get("mimeType")
                        or "application/octet-stream"
                    )
            except Exception:
                logger.warning(
                    "Failed to download external URI: room=%s message=%s uri=%s",
                    room_id, message_id, uri[:120], exc_info=True,
                )
                continue

            mime = file_info.get("mime_type") or file_info.get("mimeType") or content_type
            ext = mime.split("/")[-1] if "/" in mime else "bin"
            storage_key = f"artifacts/{room_id}/{message_id}/ext-{converted}.{ext}"

            try:
                storage = _require_storage_service()
                await storage.upload_file(
                    file_data=io.BytesIO(data),
                    s3_key=storage_key,
                    content_type=mime,
                    content_length=len(data),
                )
                orig_name = file_info.get("name")
                presigned_url = await storage.generate_presigned_url(
                    storage_key, filename=orig_name,
                )
                file_info["uri"] = presigned_url
                if part_dict.get("metadata") is None:
                    part_dict["metadata"] = {}
                part_dict["metadata"]["s3_key"] = storage_key
                converted += 1
            except Exception:
                logger.error(
                    "Failed to upload downloaded URI to storage: room=%s message=%s",
                    room_id, message_id, exc_info=True,
                )

    return converted


async def convert_pydantic_artifacts_to_s3(
    artifacts: list,
    room_id: str,
    message_id: str,
    *,
    converted_so_far: int = 0,
) -> int:
    converted = converted_so_far

    for artifact in artifacts:
        if not artifact.parts:
            continue

        for part in artifact.parts:
            root = getattr(part, "root", part)
            if getattr(root, "kind", None) != "file":
                continue
            fc = getattr(root, "file", None)
            if not fc:
                continue
            raw_bytes = getattr(fc, "bytes", None)
            if not raw_bytes:
                continue
            if converted >= MAX_INLINE_CONVERSIONS_PER_MESSAGE:
                break

            try:
                decoded = base64.b64decode(raw_bytes)
            except Exception:
                continue

            mime = getattr(fc, "mime_type", None) or "application/octet-stream"
            ext = mime.split("/")[-1] if "/" in mime else "bin"
            storage_key = f"artifacts/{room_id}/{message_id}/inline-{converted}.{ext}"

            try:
                storage = _require_storage_service()
                await storage.upload_file(
                    file_data=io.BytesIO(decoded), s3_key=storage_key,
                    content_type=mime, content_length=len(decoded),
                )
                orig_name = getattr(fc, "name", None)
                presigned_url = await storage.generate_presigned_url(
                    storage_key, filename=orig_name,
                )
                root.file = FileWithUri(
                    uri=presigned_url,
                    mime_type=getattr(fc, "mime_type", None),
                    name=orig_name,
                )
                root.metadata = {**(root.metadata or {}), "s3_key": storage_key}
                converted += 1
            except Exception:
                logger.error(
                    "Failed to upload inline base64 to storage: room=%s message=%s",
                    room_id, message_id, exc_info=True,
                )

        uri_items: list[tuple[object, object, str]] = []
        for part in artifact.parts:
            root = getattr(part, "root", part)
            if getattr(root, "kind", None) != "file":
                continue
            fc = getattr(root, "file", None)
            if not fc or getattr(fc, "bytes", None):
                continue
            uri = getattr(fc, "uri", None)
            if not uri or _is_own_storage_url(uri):
                continue
            rejection = _validate_external_uri(uri)
            if rejection:
                logger.warning(
                    "Skipping unsafe external URI (%s): room=%s message=%s",
                    rejection, room_id, message_id,
                )
                continue
            uri_items.append((root, fc, uri))

        if not uri_items:
            continue

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            max_redirects=3,
        ) as client:
            for root, fc, uri in uri_items:
                if converted >= MAX_INLINE_CONVERSIONS_PER_MESSAGE:
                    break
                try:
                    async with client.stream("GET", uri) as response:
                        if response.status_code != 200:
                            continue
                        content_length = _response_content_length(response)
                        if content_length is not None and content_length > _max_download_bytes:
                            continue
                        data = await _read_limited_response_body(
                            response,
                            _max_download_bytes,
                        )
                        if data is None:
                            continue
                        content_type = _response_content_type(response)
                except Exception:
                    logger.warning(
                        "Failed to download external URI: room=%s message=%s",
                        room_id, message_id, exc_info=True,
                    )
                    continue

                mime = getattr(fc, "mime_type", None) or content_type
                ext = mime.split("/")[-1] if "/" in mime else "bin"
                storage_key = f"artifacts/{room_id}/{message_id}/ext-{converted}.{ext}"

                try:
                    storage = _require_storage_service()
                    await storage.upload_file(
                        file_data=io.BytesIO(data), s3_key=storage_key,
                        content_type=mime, content_length=len(data),
                    )
                    orig_name = getattr(fc, "name", None)
                    presigned_url = await storage.generate_presigned_url(
                        storage_key, filename=orig_name,
                    )
                    fc.uri = presigned_url
                    root.metadata = {**(root.metadata or {}), "s3_key": storage_key}
                    converted += 1
                except Exception:
                    logger.error(
                        "Failed to upload downloaded URI to storage: room=%s message=%s",
                        room_id, message_id, exc_info=True,
                    )

    return converted


__all__ = [
    "bind_a2a_storage_dependencies",
    "convert_inline_bytes_to_s3",
    "convert_pydantic_artifacts_to_s3",
]
