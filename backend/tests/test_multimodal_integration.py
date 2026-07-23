"""Integration tests for multimodal flows (upload -> sendMessage -> verify)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.dto import RoomMessageInfo
from common.types import (
    DataPart,
    Message,
    MessageRole,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from common.utils.cancellation import CancellationToken
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

        room_svc._attachment_metadata_reader.get_for_room_file = AsyncMock(
            side_effect=mixed_reader
        )

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
            file_id="f1",
            s3_key="uploads/r/f1/photo.png",
            mime_type="image/png",
            file_name="photo.png",
            size_bytes=1024,
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
            file_id="f1",
            s3_key="uploads/r/f1/photo.png",
            mime_type="image/png",
            file_name="photo.png",
            size_bytes=1024,
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
            file_id="f1",
            s3_key="uploads/r/f1/photo.png",
            mime_type="image/png",
            file_name="photo.png",
            size_bytes=1024,
        )
        parts = await room_svc._build_message_parts("hello", [att], card)
        assert len(parts) == 2

    async def test_pdf_exact_mime_agent_accepts_pdf(self, room_svc):
        room_svc._attachment_content_reader.get_bytes = AsyncMock(return_value=b"%PDF")
        card = MagicMock()
        card.default_input_modes = ["application/pdf"]
        card.defaultInputModes = None
        att = UserAttachment(
            file_id="f2",
            s3_key="uploads/r/f2/report.pdf",
            mime_type="application/pdf",
            file_name="report.pdf",
            size_bytes=4,
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

    async def test_processor_forwards_dispatch_task_into_request(self):
        from execution.dispatch.agent_message_processor import AgentMessageProcessor
        from models.processing import ProcessingResult, ProcessingStatus

        dispatch_task = "dispatch from processor sentinel"
        room_runtime = SimpleNamespace(
            process_agent_message=AsyncMock(
                return_value=SimpleNamespace(
                    success=True,
                    a2a_message=Message(
                        role=MessageRole.USER,
                        parts=[Part(root=TextPart(text="prepared"))],
                    ),
                )
            )
        )
        room_memory_reader = SimpleNamespace(
            get_room_memory_by_room_id=AsyncMock(return_value=None)
        )
        direct_transport = SimpleNamespace(
            dispatch=AsyncMock(
                return_value=ProcessingResult(
                    ProcessingStatus.SUCCESS,
                    response_text="ok",
                )
            )
        )
        processor = AgentMessageProcessor(
            delivery=MagicMock(),
            room_runtime=room_runtime,
            room_memory_reader=room_memory_reader,
            task_tracker=MagicMock(),
            transports={"direct": direct_transport},
        )

        result = await processor.process_single_message(
            self._message(),
            "room-1",
            SimpleNamespace(agent_id="agent-1"),
            "user-msg-1",
            token=CancellationToken("agent-msg-1"),
            dispatch_task=dispatch_task,
        )

        assert result.status == ProcessingStatus.SUCCESS
        request = room_runtime.process_agent_message.await_args.args[0]
        assert isinstance(request, RoomCenterAgentMessageRequest)
        assert request.dispatch_task == dispatch_task

    async def test_request_dispatch_task_overrides_legacy_runtime_inputs(self):
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
        svc._build_room_awareness = AsyncMock(return_value=None)
        message = RoomAgentMessage(
            room_id="room-1",
            message_id="agent-msg-public",
            agent_id="agent-1",
            related_message_id="user-msg-1",
            message_content=MessageContent(message_text="public-visible-message"),
            task_content="persisted task content should not be used",
            extend_info={
                "attachment_forwarding_policy": "explicit_refs_only",
                "resolved_dispatch_resource_payloads": [
                    {
                        "ref_id": "ctx:legacy",
                        "mime_type": "text/plain",
                        "text": "legacy resource text",
                    }
                ],
                "dispatch_payload_refs": {
                    "context_refs": [],
                    "artifact_refs": [],
                    "attachment_refs": [],
                    "expected_outputs": [],
                },
            },
        )
        dispatch_task = "dispatch task text sentinel"

        result = await svc.process_agent_message(
            RoomCenterAgentMessageRequest(
                room_id=message.room_id,
                message_id=message.message_id,
                agent_id=message.agent_id,
                related_message_id=message.related_message_id,
                message=message,
                dispatch_task=dispatch_task,
                resolved_resource_payloads=[
                    {
                        "ref_id": "ctx:request",
                        "mime_type": "text/plain",
                        "text": "request resource text",
                    }
                ],
                explicit_attachment_refs=["f2"],
                attachment_forwarding_policy="explicit_refs_only",
            )
        )

        assert result.success is True
        assert result.a2a_message is not None
        texts = [
            part.root.text
            for part in result.a2a_message.parts
            if hasattr(part.root, "text")
        ]
        assert any("dispatch task text sentinel" in text for text in texts)
        assert any("request resource text" in text for text in texts)
        assert all("legacy resource text" not in text for text in texts)
        assert any(
            getattr(part.root, "file", None) is not None
            for part in result.a2a_message.parts
        )
        assert message.message_content.message_task is None
        assert message.task_content == "persisted task content should not be used"
        svc._build_room_awareness.assert_awaited_once_with(
            room_id="room-1",
            current_agent_id="agent-1",
            task_description="dispatch task text sentinel",
            agent_profiles=None,
        )
        reader.get_bytes.assert_awaited_once_with(
            "uploads/r/f2/report.pdf",
            max_bytes=1024,
        )

    async def test_json_artifact_resource_is_compiled_to_data_part(self):
        svc = RoomServices()
        attachment = UserAttachment(
            file_id="unused",
            s3_key="uploads/r/unused/file.txt",
            mime_type="text/plain",
            file_name="file.txt",
            size_bytes=1,
        )
        self._bind_runtime_dependencies(
            svc,
            attachment=attachment,
            agent_card=SimpleNamespace(
                name="Structured Agent",
                default_input_modes=["text/plain", "application/json"],
            ),
        )
        svc._build_room_awareness = AsyncMock(return_value=None)
        message = RoomAgentMessage(
            room_id="room-1",
            message_id="agent-msg-json",
            agent_id="agent-1",
            related_message_id="missing-user-message",
            message_content=MessageContent(message_text="public label"),
            task_content="public label",
            extend_info={},
        )

        result = await svc.process_agent_message(
            RoomCenterAgentMessageRequest(
                room_id=message.room_id,
                message_id=message.message_id,
                agent_id=message.agent_id,
                related_message_id=message.related_message_id,
                message=message,
                dispatch_task="Underwrite the selected submission.",
                resolved_resource_payloads=[
                    {
                        "ref_id": "broker-msg:artifact_id:submission",
                        "kind": "artifact",
                        "mime_type": "application/json",
                        "data": {
                            "client": {"name": "Acme SaaS Inc."},
                            "requested_coverage": {"currency": "GBP"},
                        },
                        "metadata": {"artifact_name": "cyber_submission"},
                    }
                ],
            )
        )

        assert result.success is True
        data_parts = [
            part.root
            for part in result.a2a_message.parts
            if isinstance(part.root, DataPart)
        ]
        assert len(data_parts) == 1
        assert data_parts[0].data["requested_coverage"]["currency"] == "GBP"
        assert data_parts[0].metadata["ref_id"] == ("broker-msg:artifact_id:submission")

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
        assert failure["message"] == "The agent does not accept an attached file type."
        assert "file_names" not in failure
        reader.get_bytes.assert_not_called()

    async def test_sensitive_filename_is_absent_after_preflight_failure_persistence(
        self,
    ):
        from execution.state.task_state_manager import TaskStateManager

        private_filename = "PRIVATE_FILENAME_SENTINEL-payroll.pdf"
        attachment = UserAttachment(
            file_id="f-private",
            s3_key=f"uploads/r/f-private/{private_filename}",
            mime_type="application/pdf",
            file_name=private_filename,
            size_bytes=4,
        )
        svc = RoomServices()
        self._bind_runtime_dependencies(
            svc,
            attachment=attachment,
            agent_card=SimpleNamespace(default_input_modes=["text"]),
        )
        message = self._message()

        result = await svc.process_agent_message(self._request(message))

        persisted_payloads = []

        async def persist(request):
            persisted_payloads.append(request.message.model_dump(mode="json"))
            return SimpleNamespace(success=True, error=None)

        room_runtime = SimpleNamespace(
            update_agent_message_by_message_id=AsyncMock(side_effect=persist)
        )
        tsm = TaskStateManager(room_runtime, MagicMock())
        failure = message.extend_info["attachment_preflight_failure"]
        await tsm.fail_pre_dispatch_task(
            message,
            error=result.error,
            error_code=failure["code"],
        )

        assert result.success is False
        assert failure == {
            "code": "agent_does_not_accept_file_type",
            "message": "The agent does not accept an attached file type.",
        }
        assert len(persisted_payloads) == 1
        assert private_filename not in json.dumps(persisted_payloads[0])

    async def test_compatible_only_policy_skips_unsupported_user_attachment(self):
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
        message.extend_info["attachment_forwarding_policy"] = "compatible_only"

        result = await svc.process_agent_message(self._request(message))

        assert result.success is True
        assert result.a2a_message is not None
        assert len(result.a2a_message.parts) == 1
        assert "attachment_preflight_failure" not in message.extend_info
        assert "skipped_user_attachments" not in message.extend_info
        assert "report.pdf" not in json.dumps(message.extend_info)
        reader.get_bytes.assert_not_called()

    async def test_explicit_refs_only_does_not_inherit_original_user_attachment(self):
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
        )
        message = self._message()
        message.extend_info["attachment_forwarding_policy"] = "explicit_refs_only"
        message.extend_info["dispatch_payload_refs"] = {
            "context_refs": [],
            "artifact_refs": [],
            "attachment_refs": [],
            "expected_outputs": [],
        }

        result = await svc.process_agent_message(self._request(message))

        assert result.success is True
        assert result.a2a_message is not None
        assert len(result.a2a_message.parts) == 1
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
