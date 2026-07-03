"""Integration tests for multimodal flows (upload -> sendMessage -> verify)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.dto import RoomMessageInfo
from common.types import (
    Message,
    MessageRole,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from models.request import RoomCenterAgentMessageRequest
from models.room import MessageContent, RoomAgentMessage, UserAttachment
from room.compat.runtime import RoomServices


@pytest.fixture
def room_svc():
    svc = RoomServices()
    svc.database_service = MagicMock()
    svc.delivery = MagicMock()
    svc._s3_service = SimpleNamespace(
        get_presigned_url=AsyncMock(return_value="https://s3/presigned")
    )
    reader = MagicMock()
    reader.get_for_room_file = AsyncMock(
        side_effect=lambda room_id, file_id: _file_meta(file_id, room_id)
    )
    svc.bind_attachment_metadata_reader(reader)
    content_reader = MagicMock()
    content_reader.get_bytes = AsyncMock(return_value=b"image-bytes")
    svc.bind_attachment_content_reader(content_reader)
    svc.bind_a2a_inline_file_limits(
        max_raw_bytes=1024 * 1024,
        max_encoded_bytes=2 * 1024 * 1024,
    )
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
        result = await room_svc._resolve_attachments(["f1"], "room1")

        from room.compat.runtime import _ResolvedAttachments
        assert isinstance(result, _ResolvedAttachments)
        assert len(result.attachments) == 1
        att = result.attachments[0]
        assert att.file_id == "f1"
        assert att.s3_key == "uploads/room1/f1/photo.png"
        assert att.mime_type == "image/png"

    async def test_content_summary_has_images(self, room_svc):
        result = await room_svc._resolve_attachments(["f1"], "room1")

        from room.compat.runtime import _ResolvedAttachments
        assert isinstance(result, _ResolvedAttachments)
        assert result.content_summary["has_images"] is True
        assert result.content_summary["attachment_count"] == 1

    async def test_mixed_content_summary(self, room_svc):
        pdf_meta = _file_meta("f2")
        pdf_meta["mime_type"] = "application/pdf"
        pdf_meta["file_name"] = "doc.pdf"

        async def mixed_reader(room_id, file_id):
            if file_id == "f1":
                return _file_meta(file_id, room_id)
            return pdf_meta

        room_svc._attachment_metadata_reader.get_for_room_file = AsyncMock(side_effect=mixed_reader)

        result = await room_svc._resolve_attachments(["f1", "f2"], "room1")

        from room.compat.runtime import _ResolvedAttachments
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
        assert parts[0].root.text == "hello"

    async def test_file_parts_added_as_inline_bytes_for_capable_agent(self, room_svc):
        card = MagicMock()
        card.default_input_modes = ["file"]
        card.defaultInputModes = None
        att = UserAttachment(
            file_id="f1", s3_key="uploads/r/f1/photo.png",
            mime_type="image/png", file_name="photo.png", size_bytes=1024,
        )

        parts = await room_svc._build_message_parts("hello", [att], card)

        assert len(parts) == 2
        assert parts[0].root.text == "hello"
        assert parts[1].root.file.name == "photo.png"
        assert parts[1].root.file.uri is None
        assert parts[1].root.file.bytes is not None
        room_svc._attachment_content_reader.get_bytes.assert_awaited_once_with(
            "uploads/r/f1/photo.png",
            max_bytes=1024 * 1024,
        )
        room_svc._s3_service.get_presigned_url.assert_not_called()

    async def test_failure_when_agent_does_not_accept_attachment_mime(self, room_svc):
        card = MagicMock()
        card.default_input_modes = ["text"]
        card.defaultInputModes = None
        att = UserAttachment(
            file_id="f1", s3_key="uploads/r/f1/photo.png",
            mime_type="image/png", file_name="photo.png", size_bytes=1024,
        )

        result = await room_svc._build_message_parts("hello", [att], card)

        from room.a2a_file_parts import AttachmentPreflightFailure

        assert isinstance(result, AttachmentPreflightFailure)
        assert result.code == "agent_does_not_accept_file_type"

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

    async def test_pdf_exact_mime_agent_accepts_pdf(self, room_svc):
        room_svc._attachment_content_reader.get_bytes = AsyncMock(return_value=b"%PDF")
        card = MagicMock()
        card.default_input_modes = ["application/pdf"]
        card.defaultInputModes = None
        att = UserAttachment(
            file_id="f2", s3_key="uploads/r/f2/report.pdf",
            mime_type="application/pdf", file_name="report.pdf", size_bytes=4,
        )

        parts = await room_svc._build_message_parts("summarize", [att], card)

        assert len(parts) == 2
        assert parts[1].root.file.mimeType == "application/pdf"
        assert parts[1].root.file.bytes is not None
        assert parts[1].root.file.uri is None


class TestProcessAgentMessageAttachmentPreflight:
    def _task_with_history(self, text: str = "summarize attachment") -> Task:
        return Task(
            id="task-1",
            status=TaskStatus(state=TaskState.submitted),
            history=[
                Message(
                    role=MessageRole.USER,
                    parts=[Part(root=TextPart(text=text))],
                )
            ],
        )

    def _message(
        self,
        *,
        message_id: str = "agent-msg-1",
        related_message_id: str = "user-msg-1",
    ) -> RoomAgentMessage:
        return RoomAgentMessage(
            room_id="room-1",
            message_id=message_id,
            agent_id="agent-1",
            related_message_id=related_message_id,
            message_content=MessageContent(
                message_task=self._task_with_history(),
            ),
            extend_info={},
        )

    def _request(self, message: RoomAgentMessage) -> RoomCenterAgentMessageRequest:
        return RoomCenterAgentMessageRequest(
            room_id=message.room_id,
            message_id=message.message_id,
            agent_id=message.agent_id,
            related_message_id=message.related_message_id,
            message=message,
        )

    def _user_message_info(self, attachment: UserAttachment) -> RoomMessageInfo:
        return RoomMessageInfo(
            room_id="room-1",
            message_id="user-msg-1",
            message_type="user",
            content={
                "message_text": "please inspect",
                "attachments": [attachment.model_dump()],
            },
        )

    def _bind_runtime_dependencies(
        self,
        svc: RoomServices,
        *,
        attachment: UserAttachment,
        agent_card,
        content: bytes = b"%PDF",
    ):
        svc.agent_service = SimpleNamespace(
            get_agent_url_by_agent_id=AsyncMock(
                return_value=SimpleNamespace(agent_url="https://agent.example")
            )
        )
        if not hasattr(agent_card, "name"):
            agent_card.name = "PDF Agent"
        svc._store = SimpleNamespace(
            get_agent_by_agent_id=AsyncMock(
                return_value=SimpleNamespace(agent_card=agent_card)
            )
        )
        svc._facade = SimpleNamespace(
            get_message=AsyncMock(return_value=self._user_message_info(attachment))
        )
        content_reader = MagicMock()
        content_reader.get_bytes = AsyncMock(return_value=content)
        svc.bind_attachment_content_reader(content_reader)
        svc.bind_a2a_inline_file_limits(max_raw_bytes=1024, max_encoded_bytes=4096)
        return content_reader

    async def test_compatible_pdf_attachment_appends_inline_bytes(self):
        svc = RoomServices()
        attachment = UserAttachment(
            file_id="f2",
            s3_key="uploads/r/f2/report.pdf",
            mime_type="application/pdf",
            file_name="report.pdf",
            size_bytes=4,
        )
        reader = self._bind_runtime_dependencies(
            svc,
            attachment=attachment,
            agent_card=SimpleNamespace(default_input_modes=["application/pdf"]),
            content=b"%PDF",
        )
        message = self._message()

        result = await svc.process_agent_message(self._request(message))

        assert result.success is True
        assert result.a2a_message is not None
        assert len(result.a2a_message.parts) == 2
        assert result.a2a_message.parts[1].root.file.bytes is not None
        assert result.a2a_message.parts[1].root.file.uri is None
        reader.get_bytes.assert_awaited_once_with(
            "uploads/r/f2/report.pdf",
            max_bytes=1024,
        )

    async def test_unsupported_attachment_returns_preflight_failure(self):
        svc = RoomServices()
        attachment = UserAttachment(
            file_id="f2",
            s3_key="uploads/r/f2/report.pdf",
            mime_type="application/pdf",
            file_name="report.pdf",
            size_bytes=4,
        )
        reader = self._bind_runtime_dependencies(
            svc,
            attachment=attachment,
            agent_card=SimpleNamespace(default_input_modes=["text"]),
        )
        message = self._message()

        result = await svc.process_agent_message(self._request(message))

        assert result.success is False
        assert result.a2a_message is None
        assert "file" in result.error.lower()
        failure = message.extend_info["attachment_preflight_failure"]
        assert failure["code"] == "agent_does_not_accept_file_type"
        assert "report.pdf" in failure["message"]
        reader.get_bytes.assert_not_called()

    async def test_oversized_declared_attachment_returns_file_too_large(self):
        svc = RoomServices()
        attachment = UserAttachment(
            file_id="f2",
            s3_key="uploads/r/f2/report.pdf",
            mime_type="application/pdf",
            file_name="report.pdf",
            size_bytes=1025,
        )
        reader = self._bind_runtime_dependencies(
            svc,
            attachment=attachment,
            agent_card=SimpleNamespace(default_input_modes=["application/pdf"]),
        )
        message = self._message()

        result = await svc.process_agent_message(self._request(message))

        assert result.success is False
        assert result.a2a_message is None
        assert message.extend_info["attachment_preflight_failure"]["code"] == (
            "file_too_large"
        )
        reader.get_bytes.assert_not_called()

    async def test_empty_attachment_bytes_returns_empty_file(self):
        svc = RoomServices()
        attachment = UserAttachment(
            file_id="f2",
            s3_key="uploads/r/f2/report.pdf",
            mime_type="application/pdf",
            file_name="report.pdf",
            size_bytes=4,
        )
        reader = self._bind_runtime_dependencies(
            svc,
            attachment=attachment,
            agent_card=SimpleNamespace(default_input_modes=["application/pdf"]),
            content=b"",
        )
        message = self._message()

        result = await svc.process_agent_message(self._request(message))

        assert result.success is False
        assert result.a2a_message is None
        assert message.extend_info["attachment_preflight_failure"]["code"] == (
            "empty_file"
        )
        reader.get_bytes.assert_awaited_once_with(
            "uploads/r/f2/report.pdf",
            max_bytes=1024,
        )
