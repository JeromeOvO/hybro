"""Shared utilities for extracting content from A2A Task/Message objects.

These are stateless, pure functions used by both WorkflowCenter
and RoomMessageCenter.
"""

import uuid
from dataclasses import dataclass, field

from a2a.types import Message, Role, Task

from common.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ExtractedParts:
    """Structured extraction result from A2A parts."""

    text_parts: list[str] = field(default_factory=list)
    file_parts: list[dict] = field(default_factory=list)
    data_parts: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def has_non_text(self) -> bool:
        return bool(self.file_parts or self.data_parts)


def extract_parts(parts: list) -> ExtractedParts:
    """Extract and classify all parts from an A2A parts list.

    Handles both direct part objects and discriminated union wrappers (part.root).
    """
    result = ExtractedParts()
    for part in parts:
        root = getattr(part, "root", part)
        kind = getattr(root, "kind", None)

        if kind == "text":
            text = getattr(root, "text", None)
            if text:
                result.text_parts.append(text)
        elif kind == "file":
            result.file_parts.append(
                root.model_dump() if hasattr(root, "model_dump") else vars(root)
            )
        elif kind == "data":
            result.data_parts.append(
                root.model_dump() if hasattr(root, "model_dump") else vars(root)
            )
        else:
            text = getattr(root, "text", None)
            if text:
                result.text_parts.append(text)
            else:
                logger.warning("Unknown part kind=%s, skipping", kind)
    return result


def extract_parts_from_artifacts(artifacts: list) -> ExtractedParts:
    """Extract parts from a list of A2A artifacts."""
    result = ExtractedParts()
    for artifact in artifacts:
        if not artifact.parts:
            continue
        artifact_parts = extract_parts(artifact.parts)
        result.text_parts.extend(artifact_parts.text_parts)
        result.file_parts.extend(artifact_parts.file_parts)
        result.data_parts.extend(artifact_parts.data_parts)
    return result


def get_text_from_a2a_response(result: Task | Message) -> str:
    """Extract text content from an A2A response (Task or Message).

    Args:
        result: A Task or Message object from A2A response

    Returns:
        Extracted text as a string, or empty string if no text found
    """
    if result.kind == "message" and hasattr(result, "parts") and result.parts:
        return get_text_from_message(result)
    elif result.kind == "task":
        message = get_message_from_task(result)
        return get_text_from_message(message) if message else ""
    return ""


def get_message_from_task(task: Task) -> Message | None:
    """Extract message from a Task object.

    Per A2A spec, task outputs should be in artifacts. We check:
    1. task.artifacts - A2A-compliant location for task outputs
    2. task.status.message - status messages (for compatibility)
    3. task.history - conversation history (fallback)
    """
    # Check task.artifacts first (A2A-compliant: task outputs go in artifacts)
    if task.artifacts:
        all_parts = []
        for artifact in task.artifacts:
            # Artifact.parts is a list of Part objects
            all_parts.extend(artifact.parts)
        if all_parts:
            logger.debug("Found %d parts in task.artifacts", len(all_parts))
            message = Message(
                role=Role.agent,
                message_id=str(uuid.uuid4()),
                task_id=task.id,
                parts=all_parts,
            )
            return message

    # Check task.status.message (for status updates, less common for final output)
    if task.status and task.status.message:
        logger.debug("Found message in task.status.message")
        return task.status.message

    # Check task.history for the last agent message (fallback)
    if task.history:
        for msg in reversed(task.history):
            if hasattr(msg, "role") and msg.role == Role.agent:
                logger.debug("Found agent message in task.history")
                return msg

    logger.warning("No message found in task %s", task.id)
    return None


def get_text_from_message(message: Message | None) -> str:
    """Extract text from a Message object. Backward-compatible wrapper."""
    if message is None:
        return ""
    return extract_parts(message.parts).text


def extract_text_from_artifacts(artifacts: list) -> str | None:
    """Extract text content from A2A artifacts. Backward-compatible wrapper."""
    text = extract_parts_from_artifacts(artifacts).text
    return text if text else None


def extract_error_message(task: Task) -> str | None:
    """Extract error message from task status."""
    if not task.status.message:
        return None
    if not task.status.message.parts:
        return None
    for part in task.status.message.parts:
        if hasattr(part, "text") and part.text:
            return part.text
        if hasattr(part, "root") and hasattr(part.root, "text"):
            return part.root.text
    return None


def extract_status_message(task: Task) -> str | None:
    """Extract human-readable status message."""
    return extract_error_message(task)  # Same extraction logic


def _is_own_s3_url(uri: str) -> bool:
    """Return True if *uri* already points to our own S3 bucket."""
    from urllib.parse import urlparse

    from config.settings import settings

    if not settings.s3_bucket_name:
        return False
    parsed = urlparse(uri)
    host = parsed.hostname or ""
    return settings.s3_bucket_name in host


def _validate_external_uri(uri: str) -> str | None:
    """Validate a URI before server-side fetch. Returns an error reason or None if safe."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

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


_MAX_DOWNLOAD_BYTES: int | None = None


def _get_max_download_bytes() -> int:
    global _MAX_DOWNLOAD_BYTES
    if _MAX_DOWNLOAD_BYTES is None:
        from config.settings import settings
        _MAX_DOWNLOAD_BYTES = settings.max_file_size_mb * 1024 * 1024
    return _MAX_DOWNLOAD_BYTES


async def convert_inline_bytes_to_s3(
    parts: list[dict],
    room_id: str,
    message_id: str,
    *,
    converted_so_far: int = 0,
) -> int:
    """Convert inline base64 file.bytes and external URIs in part dicts to S3 URIs in-place.

    Shared by ResponseProcessor (streaming finalization) and
    task_notification_service (webhook/poll completion).  Respects the
    per-message conversion cap defined in models.file_upload.

    Returns the total number of conversions performed (including
    *converted_so_far*) so callers can propagate the running count.
    """
    import base64
    import io
    import logging

    from models.file_upload import MAX_INLINE_CONVERSIONS_PER_MESSAGE
    from services.s3_service import s3_service

    logger = logging.getLogger(__name__)
    converted = converted_so_far

    # --- Pass 1: inline base64 bytes → S3 ---
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
                "Inline conversion cap (%d) reached: room=%s message=%s — skipping remaining",
                MAX_INLINE_CONVERSIONS_PER_MESSAGE, room_id, message_id,
            )
            break

        try:
            decoded = base64.b64decode(raw_bytes)
        except Exception:
            logger.warning(
                "Invalid base64 in file part: room=%s message=%s — skipping",
                room_id, message_id,
            )
            continue

        mime = file_info.get("mime_type") or file_info.get("mimeType") or "application/octet-stream"
        ext = mime.split("/")[-1] if "/" in mime else "bin"
        s3_key = f"artifacts/{room_id}/{message_id}/notify-{converted}.{ext}"

        try:
            await s3_service.upload_file(
                file_data=io.BytesIO(decoded),
                s3_key=s3_key,
                content_type=mime,
                content_length=len(decoded),
            )
            presigned_url = await s3_service.generate_presigned_url(s3_key)
            file_info["bytes"] = None
            file_info["uri"] = presigned_url
            converted += 1
        except Exception:
            logger.error(
                "Failed to upload inline file part to S3: room=%s message=%s",
                room_id, message_id, exc_info=True,
            )

    # --- Pass 2: external URIs → download & re-upload to S3 ---
    converted = await _download_external_uris_to_s3(
        parts, room_id, message_id, converted_so_far=converted,
    )

    return converted


async def _download_external_uris_to_s3(
    parts: list[dict],
    room_id: str,
    message_id: str,
    *,
    converted_so_far: int = 0,
) -> int:
    """Download file parts with external URIs and re-upload them to S3.

    Skips URIs that already point to our own S3 bucket.
    """
    import io
    import logging

    import aiohttp

    from models.file_upload import MAX_INLINE_CONVERSIONS_PER_MESSAGE
    from services.s3_service import s3_service

    logger = logging.getLogger(__name__)
    converted = converted_so_far

    uri_parts: list[tuple[dict, str]] = []
    for part in parts:
        if part.get("kind") != "file":
            continue
        file_info = part.get("file")
        if not file_info or not isinstance(file_info, dict):
            continue
        if file_info.get("bytes"):
            continue
        uri = file_info.get("uri")
        if not uri:
            continue
        if _is_own_s3_url(uri):
            continue
        rejection = _validate_external_uri(uri)
        if rejection:
            logger.warning(
                "Skipping unsafe external URI (%s): room=%s message=%s uri=%s",
                rejection, room_id, message_id, uri[:120],
            )
            continue
        uri_parts.append((file_info, uri))

    if not uri_parts:
        return converted

    max_bytes = _get_max_download_bytes()

    async with aiohttp.ClientSession() as session:
        for file_info, uri in uri_parts:
            if converted >= MAX_INLINE_CONVERSIONS_PER_MESSAGE:
                logger.warning(
                    "Conversion cap (%d) reached during URI download: room=%s message=%s",
                    MAX_INLINE_CONVERSIONS_PER_MESSAGE, room_id, message_id,
                )
                break

            try:
                async with session.get(
                    uri,
                    timeout=aiohttp.ClientTimeout(total=30),
                    allow_redirects=False,
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "External URI returned HTTP %d: room=%s message=%s uri=%s",
                            resp.status, room_id, message_id, uri[:120],
                        )
                        continue
                    cl = resp.content_length
                    if cl is not None and cl > max_bytes:
                        logger.warning(
                            "External URI Content-Length %d exceeds limit %d: room=%s message=%s",
                            cl, max_bytes, room_id, message_id,
                        )
                        continue
                    data = await resp.content.read(max_bytes + 1)
                    if len(data) > max_bytes:
                        logger.warning(
                            "External URI body exceeds size limit (%d bytes): room=%s message=%s",
                            max_bytes, room_id, message_id,
                        )
                        continue
                    content_type = resp.content_type or file_info.get("mime_type") or file_info.get("mimeType") or "application/octet-stream"
            except Exception:
                logger.warning(
                    "Failed to download external URI: room=%s message=%s uri=%s",
                    room_id, message_id, uri[:120], exc_info=True,
                )
                continue

            mime = file_info.get("mime_type") or file_info.get("mimeType") or content_type
            ext = mime.split("/")[-1] if "/" in mime else "bin"
            s3_key = f"artifacts/{room_id}/{message_id}/ext-{converted}.{ext}"

            try:
                await s3_service.upload_file(
                    file_data=io.BytesIO(data),
                    s3_key=s3_key,
                    content_type=mime,
                    content_length=len(data),
                )
                presigned_url = await s3_service.generate_presigned_url(s3_key)
                file_info["uri"] = presigned_url
                converted += 1
            except Exception:
                logger.error(
                    "Failed to upload downloaded URI to S3: room=%s message=%s",
                    room_id, message_id, exc_info=True,
                )

    return converted
