"""Attachment resolution for multimodal user messages."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from conversation.attachments import ResolvedAttachments, resolve_room_attachments


def _file_meta(file_id="f1", conversation_id="room1"):
    return {
        "file_id": file_id,
        "room_id": conversation_id,
        "s3_key": f"uploads/{conversation_id}/{file_id}/photo.png",
        "mime_type": "image/png",
        "file_name": "photo.png",
        "size_bytes": 2048,
    }


@pytest.fixture
def reader():
    reader = MagicMock()
    reader.get_for_room_file = AsyncMock(
        side_effect=lambda conversation_id, file_id: _file_meta(
            file_id, conversation_id
        )
    )
    return reader


async def test_resolve_builds_user_attachment(reader):
    result = await resolve_room_attachments(
        file_ids=["f1"], conversation_id="room1", attachment_reader=reader
    )

    assert isinstance(result, ResolvedAttachments)
    assert len(result.attachments) == 1
    attachment = result.attachments[0]
    assert attachment.file_id == "f1"
    assert attachment.mime_type == "image/png"


async def test_content_summary_has_images(reader):
    result = await resolve_room_attachments(
        file_ids=["f1"], conversation_id="room1", attachment_reader=reader
    )

    assert isinstance(result, ResolvedAttachments)
    assert result.content_summary["has_images"] is True
    assert result.content_summary["attachment_count"] == 1


async def test_mixed_content_summary(reader):
    pdf_meta = _file_meta("f2")
    pdf_meta["mime_type"] = "application/pdf"
    pdf_meta["file_name"] = "doc.pdf"

    async def mixed_reader(conversation_id, file_id):
        if file_id == "f1":
            return _file_meta(file_id, conversation_id)
        return pdf_meta

    reader.get_for_room_file = AsyncMock(side_effect=mixed_reader)

    result = await resolve_room_attachments(
        file_ids=["f1", "f2"], conversation_id="room1", attachment_reader=reader
    )

    assert isinstance(result, ResolvedAttachments)
    assert result.content_summary["has_images"] is True
    assert result.content_summary["has_files"] is True
    assert result.content_summary["attachment_count"] == 2
