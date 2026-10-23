from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from models.file_upload import MAX_ATTACHMENTS_PER_MESSAGE
from models.request import RoomCenterUserMessageRequest
from models.response import RoomCenterUserMessageResponse
from models.room import RoomUserMessage, UserAttachment


@runtime_checkable
class AttachmentMetadataReader(Protocol):
    async def get_for_room_file(self, room_id: str, file_id: str) -> dict | None: ...


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
                mime_type=file_meta["mime_type"],
                file_name=file_meta["file_name"],
                size_bytes=file_meta["size_bytes"],
                sha256=file_meta.get("sha256"),
                file_url=file_meta.get("content_url"),
            )
        )

    content_summary = None
    if attachments:
        mime_types = [attachment.mime_type for attachment in attachments]
        content_summary = {
            "has_images": any(
                mime_type.startswith("image/") for mime_type in mime_types
            ),
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
