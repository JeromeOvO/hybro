from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.protocols import AttachmentMetadataReader
from common.types import FileContent, FilePart, Part, TextPart
from models.file_upload import MAX_ATTACHMENTS_PER_MESSAGE
from models.request import RoomCenterUserMessageRequest
from models.response import RoomCenterUserMessageResponse
from models.room import RoomAgentMessage, RoomUserMessage, UserAttachment


@dataclass
class ResolvedAttachments:
    attachments: list[UserAttachment]
    content_summary: dict | None


async def resolve_room_attachments(
    *,
    file_ids: list[str],
    room_id: str,
    attachment_reader: AttachmentMetadataReader | None,
) -> ResolvedAttachments | RoomCenterUserMessageResponse:
    if len(file_ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        return RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error=f"Maximum {MAX_ATTACHMENTS_PER_MESSAGE} attachments per message",
            status_code=400,
        )
    if file_ids and attachment_reader is None:
        return RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error="Attachment resolution unavailable",
            status_code=503,
        )

    attachments: list[UserAttachment] = []
    for file_id in file_ids:
        file_meta = await attachment_reader.get_for_room_file(room_id, file_id)
        if not file_meta:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=f"File {file_id} not found",
                status_code=404,
            )
        attachments.append(
            UserAttachment(
                file_id=file_id,
                s3_key=file_meta["s3_key"],
                mime_type=file_meta["mime_type"],
                file_name=file_meta["file_name"],
                size_bytes=file_meta["size_bytes"],
            )
        )

    content_summary = None
    if attachments:
        mime_types = [attachment.mime_type for attachment in attachments]
        content_summary = {
            "has_images": any(mime_type.startswith("image/") for mime_type in mime_types),
            "has_files": any(
                not mime_type.startswith("image/") for mime_type in mime_types
            ),
            "attachment_count": len(attachments),
            "mime_types": mime_types,
        }

    return ResolvedAttachments(attachments=attachments, content_summary=content_summary)


async def resolve_and_apply_room_attachments(
    *,
    request: RoomCenterUserMessageRequest,
    user_message: RoomUserMessage,
    attachment_reader: AttachmentMetadataReader | None,
) -> RoomCenterUserMessageResponse | None:
    file_ids: list[str] = []
    seen: set[str] = set()

    if request.attachments:
        for attachment in request.attachments:
            if attachment.file_id not in seen:
                file_ids.append(attachment.file_id)
                seen.add(attachment.file_id)

    if request.inline_file_ids:
        for file_id in request.inline_file_ids:
            if file_id not in seen:
                file_ids.append(file_id)
                seen.add(file_id)

    if user_message.message_content:
        user_message.message_content.attachments = None
        user_message.message_content.content_summary = None

    if not file_ids:
        return None

    resolved = await resolve_room_attachments(
        file_ids=file_ids,
        room_id=request.room_id,
        attachment_reader=attachment_reader,
    )
    if isinstance(resolved, RoomCenterUserMessageResponse):
        return resolved
    user_message.message_content.attachments = resolved.attachments
    user_message.message_content.content_summary = resolved.content_summary
    return None


FILE_CAPABLE_EXACT = frozenset({"file", "*/*"})
FILE_CAPABLE_PREFIXES = frozenset({"image/", "audio/", "video/"})
FILE_CAPABLE_MIMES = frozenset(
    {
        "application/pdf",
        "application/octet-stream",
        "application/zip",
        "application/x-tar",
        "application/gzip",
    }
)


async def build_message_parts(
    *,
    text: str,
    attachments: list[UserAttachment] | None,
    agent_card: Any,
    object_storage,
) -> list[Part]:
    parts = [Part(root=TextPart(text=text))]
    if not attachments:
        return parts

    agent_input_modes_raw = getattr(agent_card, "default_input_modes", None)
    if agent_input_modes_raw is None:
        agent_input_modes_raw = getattr(agent_card, "defaultInputModes", None)
    agent_input_modes = set(agent_input_modes_raw or ["text"])

    supports_files = bool(
        agent_input_modes & FILE_CAPABLE_EXACT
        or agent_input_modes & FILE_CAPABLE_MIMES
        or any(
            any(mode.startswith(prefix) for prefix in FILE_CAPABLE_PREFIXES)
            for mode in agent_input_modes
        )
    )

    if supports_files:
        for attachment in attachments:
            presigned_url = await object_storage.generate_presigned_url(
                attachment.s3_key
            )
            parts.append(
                Part(
                    root=FilePart(
                        file=FileContent(
                            uri=presigned_url,
                            mimeType=attachment.mime_type,
                            name=attachment.file_name,
                        )
                    )
                )
            )
    return parts


async def refresh_artifact_presigned_urls(  # noqa: C901
    *,
    messages: list[RoomAgentMessage],
    object_storage,
) -> None:
    key_refs: list[tuple[object, str]] = []
    key_filenames: dict[str, str] = {}

    for message in messages:
        task = message.message_content.message_task if message.message_content else None
        if not task or not task.artifacts:
            continue
        for artifact in task.artifacts:
            if not artifact.parts:
                continue
            for part in artifact.parts:
                root = getattr(part, "root", part)
                if getattr(root, "kind", None) != "file":
                    continue
                metadata = getattr(root, "metadata", None)
                s3_key = metadata.get("s3_key") if isinstance(metadata, dict) else None
                if not s3_key:
                    continue
                file_content = getattr(root, "file", None)
                if file_content is None:
                    continue
                key_refs.append((file_content, s3_key))
                filename = getattr(file_content, "name", None)
                if filename:
                    key_filenames[s3_key] = filename

    if not key_refs:
        return

    url_map = await object_storage.batch_presigned_urls(
        list({key for _, key in key_refs}),
        filenames=key_filenames,
    )
    for file_content, s3_key in key_refs:
        new_url = url_map.get(s3_key)
        if new_url:
            file_content.uri = new_url
