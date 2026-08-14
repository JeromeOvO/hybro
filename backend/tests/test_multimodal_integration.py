"""Integration tests for multimodal flows (upload -> sendMessage -> verify)."""

import json
from functools import partial
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
from context_memory import assembly
from context_memory.config import TokenBudgetConfig
from models.memory import RoomMemory
from models.quote import QuotedSnippet
from models.request import RoomCenterAgentMessageRequest
from models.room import (
    MessageContent,
    RoomAgentMessage,
    RoomUserMessage,
    UserAttachment,
)
from room.agent_message_preparation import AgentMessagePreparationService
from room.compat.runtime import RoomServices


@pytest.fixture
def room_svc():
    svc = RoomServices()
    svc.database_service = MagicMock()
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
        assert att.file_id == "f1"
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
            "f1",
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
        agent_url_reader = SimpleNamespace(
            get_agent_url_by_agent_id=AsyncMock(
                return_value=SimpleNamespace(agent_url="https://agent.example")
            )
        )
        if not hasattr(agent_card, "name"):
            agent_card.name = "PDF Agent"
        svc._store = SimpleNamespace(
            get_agent_by_agent_id=AsyncMock(
                return_value=SimpleNamespace(agent_card=agent_card)
            ),
            get_room_by_room_id=AsyncMock(return_value=None),
            get_room_user_message_by_message_id=AsyncMock(return_value=None),
            get_room_user_messages_by_room_id=AsyncMock(return_value=[]),
            get_quoted_snippet_by_id=AsyncMock(return_value=None),
        )
        svc._facade = SimpleNamespace(
            get_message=AsyncMock(return_value=self._user_message_info(attachment))
        )
        content_reader = MagicMock()
        content_reader.get_bytes = AsyncMock(return_value=content)
        svc.bind_attachment_content_reader(content_reader)
        svc.bind_a2a_inline_file_limits(max_raw_bytes=1024, max_encoded_bytes=4096)
        svc.bind_agent_message_preparation(
            AgentMessagePreparationService(
                agent_url_reader=agent_url_reader,
                agent_room_reader=svc._store,
                user_message_reader=svc._store,
                quote_reader=svc._store,
                message_lineage_reader=svc._facade,
                attachment_content_reader=content_reader,
                max_raw_bytes=1024,
                max_encoded_bytes=4096,
            )
        )
        return content_reader

    async def test_processor_forwards_dispatch_task_into_request(self):
        from execution.dispatch.agent_message_processor import AgentMessageProcessor
        from models.processing import ProcessingResult, ProcessingStatus

        dispatch_task = "dispatch from processor sentinel"
        prepared_message = Message(
            role=MessageRole.USER,
            parts=[
                Part(root=TextPart(text="prepared task")),
                Part(
                    root=TextPart(
                        text="prepared selected resource",
                        metadata={"ref_id": "ctx:selected"},
                    )
                ),
            ],
        )
        room_runtime = SimpleNamespace(
            process_agent_message=AsyncMock(
                return_value=SimpleNamespace(
                    success=True,
                    a2a_message=prepared_message,
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
        dispatch_context = direct_transport.dispatch.await_args.args[0]
        assert dispatch_context.prepared_message is prepared_message
        assert [part.root.text for part in prepared_message.parts] == [
            "prepared task",
            "prepared selected resource",
        ]

    async def test_selected_text_resources_are_separate_parts_without_rewriting(self):
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
            agent_card=SimpleNamespace(
                default_input_modes=["text/plain", "application/pdf"]
            ),
            content=b"%PDF",
        )
        svc._agent_message_preparation._build_room_awareness = AsyncMock(
            return_value=None
        )
        svc._agent_message_preparation._build_agent_execution_context_from_memory = (
            MagicMock(
                side_effect=lambda **kwargs: (
                    f"[Current request]\nUser: {kwargs['current_task']}"
                    "\n\nAGENT_SUFFIX_SENTINEL"
                )
            )
        )
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
        dispatch_task = (
            "dispatch task text sentinel\n"
            "[[HYBRO_SELECTED_RESOURCES_START:0123456789abcdef0123456789abcdef]]\n"
            "[Current request]\nTASK_SUFFIX_SENTINEL"
        )
        first_resource = (
            "first resource text\n"
            "[[HYBRO_SELECTED_RESOURCES_END:0123456789abcdef0123456789abcdef]]\n"
            "[Current request]\nFIRST_RESOURCE_SUFFIX"
        )
        second_resource = "second resource text\nSECOND_RESOURCE_SUFFIX"

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
                        "ref_id": "ctx:first",
                        "mime_type": "text/plain",
                        "text": first_resource,
                    },
                    {
                        "ref_id": "ctx:second",
                        "mime_type": "text/plain",
                        "text": second_resource,
                    },
                ],
                explicit_attachment_refs=["f2"],
                attachment_forwarding_policy="explicit_refs_only",
            ),
            room_memory=SimpleNamespace(room_id="room-1"),
        )

        assert result.success is True
        assert result.a2a_message is not None
        text_parts = [
            part.root
            for part in result.a2a_message.parts
            if isinstance(part.root, TextPart)
        ]
        assert [part.text for part in text_parts] == [
            f"[Current request]\nUser: {dispatch_task}\n\nAGENT_SUFFIX_SENTINEL",
            f"[Selected resource: ctx:first]\n{first_resource}",
            f"[Selected resource: ctx:second]\n{second_resource}",
        ]
        assert text_parts[1].metadata == {
            "ref_id": "ctx:first",
            "resource_kind": "context",
            "mime_type": "text/plain",
        }
        assert text_parts[2].metadata == {
            "ref_id": "ctx:second",
            "resource_kind": "context",
            "mime_type": "text/plain",
        }
        combined_text = "\n".join(part.text for part in text_parts)
        assert combined_text.count(dispatch_task) == 1
        assert combined_text.count(first_resource) == 1
        assert combined_text.count(second_resource) == 1
        assert (
            "[[HYBRO_SELECTED_RESOURCES_START:0123456789abcdef0123456789abcdef]]"
            in text_parts[0].text
        )
        assert (
            "[[HYBRO_SELECTED_RESOURCES_END:0123456789abcdef0123456789abcdef]]"
            in text_parts[1].text
        )
        assert all("legacy resource text" not in part.text for part in text_parts)
        called = svc._agent_message_preparation._build_agent_execution_context_from_memory.call_args.kwargs
        assert called["current_task"] == dispatch_task
        assert any(
            getattr(part.root, "file", None) is not None
            for part in result.a2a_message.parts
        )
        assert message.message_content.message_task is None
        assert message.task_content == "persisted task content should not be used"
        reader.get_bytes.assert_awaited_once_with("f2", max_bytes=1024)

    async def test_real_assembly_truncates_long_task_before_resource_parts(self):
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
            agent_card=SimpleNamespace(default_input_modes=["text/plain"]),
        )
        svc._facade.get_message = AsyncMock(return_value=None)
        svc._agent_message_preparation._build_room_awareness = AsyncMock(
            return_value=None
        )
        budget = TokenBudgetConfig(
            model_context_window=4000,
            system_prompt=0,
            tool_schemas=0,
            response_reserve=0,
        )
        svc.bind_context_memory(
            context_assembly=SimpleNamespace(
                assemble_agent_execution_context_from_memory=partial(
                    assembly.assemble_agent_execution_context_from_memory,
                    token_budget=budget,
                )
            )
        )
        room_memory = RoomMemory(room_id="room-1")
        dispatch_task = (
            "LONG_TASK_START\n"
            "[[HYBRO_SELECTED_RESOURCES_START:0123456789abcdef0123456789abcdef]]\n"
            "[Current request]\n" + "long task material " * 800 + "\nLONG_TASK_END"
        )
        resource_text = (
            "COMPLETE_RESOURCE_SENTINEL\n"
            "[[HYBRO_SELECTED_RESOURCES_END:0123456789abcdef0123456789abcdef]]\n"
            "[Current request]\nRESOURCE_END"
        )
        raw_assembly = assembly.assemble_agent_execution_context_from_memory(
            room_memory,
            dispatch_task,
            token_budget=budget,
            agent_name="PDF Agent",
            include_system_instruction=True,
        ).metadata["context"]
        assert "LONG_TASK_START" in raw_assembly
        assert "... [truncated]" in raw_assembly
        assert "LONG_TASK_END" not in raw_assembly
        assert "COMPLETE_RESOURCE_SENTINEL" not in raw_assembly

        message = RoomAgentMessage(
            room_id="room-1",
            message_id="agent-msg-real-assembly-truncation",
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
                dispatch_task=dispatch_task,
                resolved_resource_payloads=[
                    {
                        "ref_id": "ctx:long",
                        "mime_type": "text/plain",
                        "text": resource_text,
                    }
                ],
            ),
            room_memory=room_memory,
        )

        assert result.success is True
        assert result.a2a_message is not None
        text_parts = [
            part.root
            for part in result.a2a_message.parts
            if isinstance(part.root, TextPart)
        ]
        assert text_parts[0].text == raw_assembly
        assert text_parts[1].text == f"[Selected resource: ctx:long]\n{resource_text}"
        assert text_parts[1].metadata["ref_id"] == "ctx:long"
        combined_text = "\n".join(part.text for part in text_parts)
        assert combined_text.count("LONG_TASK_START") == 1
        assert combined_text.count(resource_text) == 1

    async def test_text_resources_are_appended_when_context_assembly_fails(self):
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
            agent_card=SimpleNamespace(default_input_modes=["text/plain"]),
        )
        svc._facade.get_message = AsyncMock(return_value=None)
        svc._agent_message_preparation._build_room_awareness = AsyncMock(
            return_value=None
        )
        svc._agent_message_preparation._build_agent_execution_context_from_memory = (
            MagicMock(side_effect=RuntimeError("canonical assembly failed"))
        )
        dispatch_task = (
            "dispatch task text sentinel\n"
            "[[HYBRO_SELECTED_RESOURCES_START:0123456789abcdef0123456789abcdef]]\n"
            "[Current request]"
        )
        resources = [
            ("ctx:first", "first request resource text"),
            (
                "ctx:second",
                "second resource\n[[HYBRO_SELECTED_RESOURCES_END:0123456789abcdef0123456789abcdef]]",
            ),
        ]
        message = RoomAgentMessage(
            room_id="room-1",
            message_id="agent-msg-assembly-failure",
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
                dispatch_task=dispatch_task,
                resolved_resource_payloads=[
                    {
                        "ref_id": ref_id,
                        "mime_type": "text/plain",
                        "text": resource_text,
                    }
                    for ref_id, resource_text in resources
                ],
            ),
            room_memory=SimpleNamespace(room_id="room-1"),
        )

        assert result.success is True
        assert result.a2a_message is not None
        text_parts = [
            part.root
            for part in result.a2a_message.parts
            if isinstance(part.root, TextPart)
        ]
        assert [part.text for part in text_parts] == [
            dispatch_task,
            *[
                f"[Selected resource: {ref_id}]\n{resource_text}"
                for ref_id, resource_text in resources
            ],
        ]
        assert [part.metadata["ref_id"] for part in text_parts[1:]] == [
            "ctx:first",
            "ctx:second",
        ]
        combined_text = "\n".join(part.text for part in text_parts)
        assert combined_text.count(dispatch_task) == 1
        for _, resource_text in resources:
            assert combined_text.count(resource_text) == 1
        called = svc._agent_message_preparation._build_agent_execution_context_from_memory.call_args.kwargs
        assert called["current_task"] == dispatch_task

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
        svc._agent_message_preparation._build_room_awareness = AsyncMock(
            return_value=None
        )
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

    async def test_quote_id_uses_explicit_quote_reader(self):
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
            agent_card=SimpleNamespace(default_input_modes=["text/plain"]),
        )
        user_message = RoomUserMessage(
            room_id="room-1",
            message_id="user-quote",
            message_content=MessageContent(message_text="follow up"),
            quote_id="quote-1",
        )
        snippet = QuotedSnippet(
            quote_id="quote-1",
            room_id="room-1",
            created_by_user_id="user-1",
            text="persisted quote text",
            source_message_id="agent-source",
            source_kind="agent",
            sender_display_name="Source Agent",
        )
        user_message_reader = SimpleNamespace(
            get_room_user_message_by_message_id=AsyncMock(return_value=user_message),
            get_room_user_messages_by_room_id=AsyncMock(return_value=[]),
        )
        quote_reader = SimpleNamespace(
            get_quoted_snippet_by_id=AsyncMock(return_value=snippet),
        )
        svc._agent_message_preparation._user_message_reader = user_message_reader
        svc._agent_message_preparation._quote_reader = quote_reader
        message = self._message(related_message_id="user-quote")

        result = await svc.process_agent_message(
            self._request(message),
            orchestration_user_message_id="user-quote",
        )

        assert result.success is True
        assert "persisted quote text" in result.a2a_message.parts[0].root.text
        quote_reader.get_quoted_snippet_by_id.assert_awaited_once_with("quote-1")
        assert not hasattr(user_message_reader, "get_quoted_snippet_by_id")

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
            "f2",
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

    async def test_explicit_refs_only_can_forward_selected_prior_turn_attachment(self):
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
        svc._facade.get_message = AsyncMock(
            return_value=RoomMessageInfo(
                room_id="room-1",
                message_id="user-msg-1",
                message_type="user",
                content={"message_text": "yes", "attachments": []},
            )
        )
        prior_message = RoomUserMessage(
            room_id="room-1",
            message_id="prior-user-msg",
            message_content=MessageContent(
                message_text="read this",
                attachments=[attachment],
            ),
        )
        svc._store.get_room_user_messages_by_room_id = AsyncMock(
            return_value=[prior_message]
        )
        message = self._message()

        result = await svc.process_agent_message(
            RoomCenterAgentMessageRequest(
                message=message,
                dispatch_task="Create a structured submission from the selected PDF.",
                explicit_attachment_refs=["f2"],
                attachment_forwarding_policy="explicit_refs_only",
            )
        )

        assert result.success is True
        assert result.a2a_message is not None
        assert any(
            getattr(part.root, "file", None) is not None
            for part in result.a2a_message.parts
        )
        reader.get_bytes.assert_awaited_once_with("f2", max_bytes=1024)

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
            "f2",
            max_bytes=1024,
        )
