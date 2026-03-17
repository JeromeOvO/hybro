"""Integration tests for multimodal flows (upload -> sendMessage -> verify)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models.file_upload import FileUploadMetadata, FileUploadResponse
from models.request import RoomCenterUserMessageRequest, UserAttachmentRequest
from models.room import MessageContent, RoomUserMessage, UserAttachment
from services.room_services import RoomServices


@pytest.fixture
def room_svc():
    svc = RoomServices()
    svc.database_service = MagicMock()
    svc.sse_manager = MagicMock()
    svc.room_memory_service = MagicMock()
    svc._s3_service = AsyncMock()
    svc._s3_service.generate_presigned_url = AsyncMock(return_value="https://s3/presigned")
    return svc


def _file_meta(file_id="f1", room_id="room1"):
    return {
        "file_id": file_id,
        "room_id": room_id,
        "s3_key": f"uploads/{room_id}/{file_id}/photo.png",
        "mime_type": "image/png",
        "file_name": "photo.png",
        "size_bytes": 2048,
    }


class TestUploadToSendFlow:
    """Tests the flow: file uploaded -> attachment referenced in message -> resolved."""

    async def test_resolve_builds_user_attachment(self, room_svc):
        with patch("database.mongodb.mongodb") as mock_db:
            mock_db.file_uploads_collection.find_one = AsyncMock(return_value=_file_meta("f1"))
            result = await room_svc._resolve_attachments(["f1"], "room1")

        from services.room_services import _ResolvedAttachments
        assert isinstance(result, _ResolvedAttachments)
        assert len(result.attachments) == 1
        att = result.attachments[0]
        assert att.file_id == "f1"
        assert att.s3_key == "uploads/room1/f1/photo.png"
        assert att.mime_type == "image/png"

    async def test_content_summary_has_images(self, room_svc):
        with patch("database.mongodb.mongodb") as mock_db:
            mock_db.file_uploads_collection.find_one = AsyncMock(return_value=_file_meta("f1"))
            result = await room_svc._resolve_attachments(["f1"], "room1")

        from services.room_services import _ResolvedAttachments
        assert isinstance(result, _ResolvedAttachments)
        assert result.content_summary["has_images"] is True
        assert result.content_summary["attachment_count"] == 1

    async def test_mixed_content_summary(self, room_svc):
        pdf_meta = _file_meta("f2")
        pdf_meta["mime_type"] = "application/pdf"
        pdf_meta["file_name"] = "doc.pdf"

        with patch("database.mongodb.mongodb") as mock_db:
            mock_db.file_uploads_collection.find_one = AsyncMock(
                side_effect=lambda q: _file_meta(q["file_id"]) if q["file_id"] == "f1" else pdf_meta
            )
            result = await room_svc._resolve_attachments(["f1", "f2"], "room1")

        from services.room_services import _ResolvedAttachments
        assert isinstance(result, _ResolvedAttachments)
        assert result.content_summary["has_images"] is True
        assert result.content_summary["has_files"] is True
        assert result.content_summary["attachment_count"] == 2


class TestBuildMessageParts:
    """Tests _build_message_parts for A2A multimodal dispatch."""

    async def test_text_only_when_no_attachments(self, room_svc):
        card = MagicMock()
        card.default_input_modes = ["text"]
        parts = await room_svc._build_message_parts("hello", None, card)
        assert len(parts) == 1
        assert parts[0].text == "hello"

    async def test_file_parts_added_for_capable_agent(self, room_svc):
        card = MagicMock()
        card.default_input_modes = ["file"]
        card.defaultInputModes = None
        att = UserAttachment(
            file_id="f1", s3_key="uploads/r/f1/photo.png",
            mime_type="image/png", file_name="photo.png", size_bytes=1024,
        )
        parts = await room_svc._build_message_parts("hello", [att], card)
        assert len(parts) == 2
        assert parts[0].text == "hello"
        assert hasattr(parts[1], "file")

    async def test_text_only_when_agent_not_file_capable(self, room_svc):
        card = MagicMock()
        card.default_input_modes = ["text"]
        card.defaultInputModes = None
        att = UserAttachment(
            file_id="f1", s3_key="uploads/r/f1/photo.png",
            mime_type="image/png", file_name="photo.png", size_bytes=1024,
        )
        parts = await room_svc._build_message_parts("hello", [att], card)
        assert len(parts) == 1

    async def test_wildcard_agent_accepts_files(self, room_svc):
        card = MagicMock()
        card.default_input_modes = ["*/*"]
        card.defaultInputModes = None
        att = UserAttachment(
            file_id="f1", s3_key="uploads/r/f1/photo.png",
            mime_type="image/png", file_name="photo.png", size_bytes=1024,
        )
        parts = await room_svc._build_message_parts("hello", [att], card)
        assert len(parts) == 2
