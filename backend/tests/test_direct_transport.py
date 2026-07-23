"""
Unit tests for DirectTransport module.

Tests cover:
- _parse_sync_fallback_response: None input, message kind, task kind,
  normalized error, and default fallback
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.a2a_constants import CommonTaskState
from common.types import (
    Artifact,
    FileContent,
    FilePart,
    Message,
    MessageRole,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from execution.dispatch.agent_event import AgentEvent
from execution.dispatch.dispatch_middleware import DispatchContext
from execution.dispatch.response_handler import AgentResponseHandler
from execution.dispatch.transports import direct as direct_module
from execution.dispatch.transports.direct import DirectTransport, MessageStreamingState
from models.error import A2AServiceError
from models.processing import ProcessingContext, ProcessingStatus
from models.room import MessageContent, RoomAgentMessage

# =============================================================================
# _parse_sync_fallback_response Tests
# =============================================================================


class TestResolveTaskResponseStatus:
    def test_requires_auth_without_status(self):
        status = DirectTransport._resolve_task_response_status({"requires_auth": True})
        assert status == CommonTaskState.AUTH_REQUIRED

    def test_requires_input_without_status(self):
        status = DirectTransport._resolve_task_response_status({"requires_input": True})
        assert status == CommonTaskState.INPUT_REQUIRED

    def test_requires_auth_overrides_working_status(self):
        status = DirectTransport._resolve_task_response_status(
            {"requires_auth": True, "status": "working"}
        )
        assert status == CommonTaskState.AUTH_REQUIRED


class TestParseSyncFallbackResponse:
    """Tests for sync response parsing into normalized dict."""

    def test_returns_empty_for_none(self):
        result = DirectTransport._parse_sync_fallback_response(None, "msg-1")
        assert result == {"type": "message", "message_id": "msg-1", "content": ""}

    def test_parses_message_kind(self):
        response = {
            "kind": "message",
            "result": {
                "kind": "message",
                "role": "agent",
                "messageId": "agent-msg-1",
                "parts": [{"kind": "text", "text": "Hello"}],
            },
            "error": None,
        }

        result = DirectTransport._parse_sync_fallback_response(response, "msg-1")
        assert result["type"] == "message"
        assert result["content"] == "Hello"

    def test_parses_task_kind(self):
        response = {
            "kind": "task",
            "result": {
                "kind": "task",
                "id": "task-001",
                "status": {"state": "completed"},
                "artifacts": [],
            },
            "error": None,
        }

        result = DirectTransport._parse_sync_fallback_response(response, "msg-1")
        assert result["type"] == "task"
        assert result["task_id"] == "task-001"
        assert result["status"] == "completed"

    def test_terminal_failed_task_with_artifacts_uses_projected_public_error(self):
        private_sentinel = "PRIVATE_SENTINEL_sync_terminal_failed_artifact"
        response = {
            "kind": "task",
            "result": {
                "kind": "task",
                "id": "task-001",
                "contextId": "ctx-001",
                "status": {
                    "state": "failed",
                    "message": {
                        "kind": "message",
                        "role": "agent",
                        "messageId": "private-status",
                        "parts": [{"kind": "text", "text": private_sentinel}],
                    },
                },
                "history": [
                    {
                        "kind": "message",
                        "role": "agent",
                        "messageId": "private-history",
                        "parts": [{"kind": "text", "text": private_sentinel}],
                    }
                ],
                "artifacts": [
                    {
                        "artifactId": "partial-artifact",
                        "name": "partial",
                        "parts": [{"kind": "text", "text": private_sentinel}],
                    }
                ],
                "metadata": {"remote_error": private_sentinel},
            },
            "error": None,
        }

        result = DirectTransport._parse_sync_fallback_response(response, "msg-1")

        assert result == {
            "type": "message",
            "message_id": "msg-1",
            "content": None,
            "status": "failed",
            "error": "Task failed",
        }
        assert private_sentinel not in json.dumps(result)

    def test_terminal_completed_task_with_inline_file_bytes_drops_unaddressable_part(
        self,
    ):
        private_sentinel = "PRIVATE_SENTINEL_sync_terminal_file_bytes"
        response = {
            "kind": "task",
            "result": {
                "kind": "task",
                "id": "task-001",
                "contextId": "ctx-001",
                "status": {"state": "completed"},
                "artifacts": [
                    {
                        "artifactId": "file-artifact",
                        "name": "result-file",
                        "parts": [
                            {
                                "kind": "file",
                                "file": {
                                    "bytes": private_sentinel,
                                    "mimeType": "text/plain",
                                    "name": "result.txt",
                                },
                            }
                        ],
                    }
                ],
            },
            "error": None,
        }

        result = DirectTransport._parse_sync_fallback_response(response, "msg-1")

        assert result == {
            "type": "message",
            "message_id": "msg-1",
            "task_id": "task-001",
            "status": "completed",
            "content": None,
        }
        assert private_sentinel not in json.dumps(result)

    def test_parses_interactive_task_with_requires_flags(self):
        response = {
            "kind": "task",
            "result": {
                "kind": "task",
                "id": "task-001",
                "status": {"state": "auth-required"},
                "artifacts": [],
            },
            "error": None,
        }

        result = DirectTransport._parse_sync_fallback_response(response, "msg-1")

        assert result["type"] == "task"
        assert result["status"] == "auth-required"
        assert result["requires_auth"] is True
        assert result["requires_input"] is False

    def test_raises_on_normalized_error(self):
        response = {
            "kind": "error",
            "result": None,
            "error": {"code": -32000, "message": "Agent offline"},
        }

        with pytest.raises(A2AServiceError):
            DirectTransport._parse_sync_fallback_response(response, "msg-1")

    def test_unknown_kind_returns_empty(self):
        response = {"kind": "unknown", "result": {"kind": "unknown"}, "error": None}

        with pytest.raises(A2AServiceError):
            DirectTransport._parse_sync_fallback_response(response, "msg-1")

    def test_concatenates_multiple_text_parts(self):
        response = {
            "kind": "message",
            "result": {
                "kind": "message",
                "role": "agent",
                "messageId": "agent-msg-1",
                "parts": [
                    {"kind": "text", "text": "Hello "},
                    {"kind": "text", "text": "world"},
                ],
            },
            "error": None,
        }

        result = DirectTransport._parse_sync_fallback_response(response, "msg-1")
        assert result["content"] == "Hello world"

    def test_rejects_sdk_envelope_shape(self):
        response = MagicMock()
        response.root = MagicMock()

        with pytest.raises(A2AServiceError, match="normalized dict"):
            DirectTransport._parse_sync_fallback_response(response, "msg-1")

    def test_stream_error_message_ignores_sdk_envelope_shape(self):
        response = MagicMock()
        response.root.error.message = "Agent offline"

        assert DirectTransport._stream_error_message(response) is None
        assert (
            DirectTransport._stream_error_message(
                {"kind": "error", "error": {"message": "Agent offline"}}
            )
            == "{'message': 'Agent offline'}"
        )

    def test_coerce_parts_preserves_snake_case_file_mime_type(self):
        parts = DirectTransport._coerce_parts(
            [
                {
                    "kind": "file",
                    "file": {
                        "uri": "s3://bucket/file.png",
                        "mime_type": "image/png",
                    },
                }
            ]
        )

        assert parts[0].root.file.mimeType == "image/png"

    def test_coerce_stream_result_accepts_sdk_event_aliases(self):
        status_event = DirectTransport._coerce_stream_result(
            {
                "kind": "status-update",
                "result": {
                    "kind": "status-update",
                    "taskId": "task-1",
                    "status": {"state": "completed"},
                    "final": True,
                },
            }
        )
        artifact_event = DirectTransport._coerce_stream_result(
            {
                "kind": "artifact-update",
                "result": {
                    "kind": "artifact-update",
                    "taskId": "task-1",
                    "artifact": {
                        "artifactId": "art-1",
                        "parts": [{"kind": "text", "text": "hello"}],
                    },
                    "append": True,
                    "lastChunk": True,
                },
            }
        )

        assert isinstance(status_event, TaskStatusUpdateEvent)
        assert status_event.id == "task-1"
        assert status_event.task_id == "task-1"
        assert isinstance(artifact_event, TaskArtifactUpdateEvent)
        assert artifact_event.id == "task-1"
        assert artifact_event.task_id == "task-1"
        assert artifact_event.append is True
        assert artifact_event.last_chunk is True

    def test_coerce_stream_result_defaults_nullable_artifact_flags(self):
        artifact_event = DirectTransport._coerce_stream_result(
            {
                "kind": "artifact-update",
                "result": {
                    "kind": "artifact-update",
                    "taskId": "task-1",
                    "artifact": {
                        "artifactId": "art-1",
                        "parts": [{"kind": "text", "text": "hello"}],
                    },
                    "append": None,
                    "lastChunk": None,
                },
            }
        )

        assert isinstance(artifact_event, TaskArtifactUpdateEvent)
        assert artifact_event.append is False
        assert artifact_event.last_chunk is False


# =============================================================================
# Instance-method tests — bypass __init__ via object.__new__, inject mocks
# =============================================================================


def _make_processor(**overrides):
    """Create a DirectTransport with mocked dependencies, bypassing __init__."""
    proc = object.__new__(DirectTransport)
    proc.response_handler = overrides.get("response_handler", MagicMock())
    proc.tsm = overrides.get("tsm", MagicMock())
    proc.delivery = overrides.get("delivery", MagicMock())
    proc.a2a_transport = overrides.get("a2a_transport", MagicMock())
    proc.remote_task_reader = overrides.get("remote_task_reader", MagicMock())
    db = overrides.get("database_service", MagicMock())
    proc._message_reader = db
    proc._artifact_store = db
    proc._task_updater = db
    proc._s3_service = overrides.get("s3_service", MagicMock())
    return proc


def _make_room_agent_message(**overrides):
    """Build a minimal RoomAgentMessage with sensible defaults."""
    task = Task(
        id="task-001",
        contextId="ctx-001",
        status=TaskStatus(state=TaskState.working),
        kind="task",
    )
    content = MessageContent(message_text="", message_task=task)

    defaults = dict(
        room_id="room-1",
        message_id="msg-1",
        message_type="agent",
        agent_id="agent-1",
        user_id="user-1",
        related_message_id="user-msg-1",
        message_content=content,
    )
    defaults.update(overrides)
    return RoomAgentMessage(**defaults)


@pytest.mark.asyncio
async def test_emit_terminal_uses_public_task_output_not_legacy_message_text():
    private_sentinel = "PRIVATE_SENTINEL_terminal_message_text"
    public_output = "Final public terminal output"
    task = Task(
        id="task-terminal",
        status=TaskStatus(state=TaskState.completed),
        artifacts=[
            {
                "artifactId": "artifact-terminal",
                "parts": [{"kind": "text", "text": public_output}],
            }
        ],
    )
    message = _make_room_agent_message(
        message_content=MessageContent(
            message_text=private_sentinel,
            message_task=task,
        ),
        extend_info={"public_task_label": "Requesting Insurer"},
    )
    response_handler = MagicMock(handle=AsyncMock())
    transport = _make_processor(response_handler=response_handler)
    ctx = ProcessingContext(
        room_id="room-1",
        current_message=message,
        agent_card=MagicMock(name="Insurer"),
        user_message_id="user-msg-1",
    )

    await transport._emit_terminal(ctx, CommonTaskState.COMPLETED)

    emitted_event = response_handler.handle.await_args.args[0]
    assert emitted_event.text == public_output
    assert private_sentinel not in json.dumps(emitted_event.__dict__)


@pytest.mark.asyncio
async def test_streaming_exception_uses_generic_public_failure_everywhere():
    private_sentinel = "PRIVATE_SENTINEL_streaming_exception_dispatch_task"
    message = _make_room_agent_message()
    response_handler = MagicMock(handle=AsyncMock())
    tsm = MagicMock(transition_task=AsyncMock())
    delivery = MagicMock(send_error=AsyncMock())
    capability_issues = MagicMock(record_issue=AsyncMock())
    transport = _make_processor(
        response_handler=response_handler,
        tsm=tsm,
        delivery=delivery,
    )
    transport.capability_issue_service = capability_issues
    transport.a2a_transport.has_streaming_capability.return_value = True
    transport.handle_streaming_response = AsyncMock(
        side_effect=RuntimeError(private_sentinel)
    )
    agent = MagicMock()
    agent.agent_card.name = "Claims Agent"
    ctx = DispatchContext(
        agent=agent,
        room_agent_message=message,
        room_id=message.room_id,
        user_message_id=message.related_message_id,
        prepared_message=MagicMock(),
    )

    result = await transport.dispatch(ctx, message)

    transition = tsm.transition_task.await_args
    assert transition.args[1] == CommonTaskState.FAILED
    assert transition.kwargs["error"] == "Agent processing failed"
    emitted_event = response_handler.handle.await_args.args[0]
    assert emitted_event.error_text == "Agent processing failed"
    delivery.send_error.assert_awaited_once_with(
        message.room_id,
        "Agent processing failed",
        message_id=message.message_id,
    )
    assert result.status == ProcessingStatus.FAILED
    assert result.response_text == "Agent processing failed"
    assert result.status_message == "agent_execution_failed"
    capability_issues.record_issue.assert_awaited_once()
    assert (
        private_sentinel
        in capability_issues.record_issue.await_args.kwargs["error_message"]
    )
    public_payload = json.dumps(
        {
            "transition": transition.kwargs,
            "event": emitted_event.__dict__,
            "delivery": delivery.send_error.await_args.kwargs,
            "result": result.__dict__,
        },
        default=str,
    )
    assert private_sentinel not in public_payload


@pytest.mark.asyncio
async def test_sync_exception_uses_generic_public_failure_everywhere():
    private_sentinel = "PRIVATE_SENTINEL_sync_exception_dispatch_task"
    message = _make_room_agent_message()
    response_handler = MagicMock(handle=AsyncMock())
    tsm = MagicMock(transition_task=AsyncMock())
    delivery = MagicMock(send_error=AsyncMock())
    capability_issues = MagicMock(record_issue=AsyncMock())
    transport = _make_processor(
        response_handler=response_handler,
        tsm=tsm,
        delivery=delivery,
    )
    transport.capability_issue_service = capability_issues
    transport.a2a_transport.has_streaming_capability.return_value = False
    transport.a2a_transport.send_message_to_tracked_agent = AsyncMock(
        side_effect=RuntimeError(private_sentinel)
    )
    agent = MagicMock()
    agent.agent_card.name = "Claims Agent"
    processing_ctx = ProcessingContext(
        room_id=message.room_id,
        current_message=message,
        agent_card=agent.agent_card,
        user_message_id=message.related_message_id,
        task_info={"webhook_token": "token", "context_id": "context"},
    )
    transport._setup_tracking_context = AsyncMock(
        return_value=(processing_ctx.task_info, processing_ctx)
    )
    ctx = DispatchContext(
        agent=agent,
        room_agent_message=message,
        room_id=message.room_id,
        user_message_id=message.related_message_id,
        prepared_message=MagicMock(),
    )

    result = await transport.dispatch(ctx, message)

    transition = tsm.transition_task.await_args
    assert transition.args[1] == CommonTaskState.FAILED
    assert transition.kwargs["error"] == "Agent processing failed"
    emitted_event = response_handler.handle.await_args.args[0]
    assert emitted_event.error_text == "Agent processing failed"
    delivery.send_error.assert_awaited_once_with(
        message.room_id,
        "Agent processing failed",
        message_id=message.message_id,
    )
    assert result.status == ProcessingStatus.FAILED
    assert result.response_text == "Agent processing failed"
    assert result.status_message == "agent_execution_failed"
    capability_issues.record_issue.assert_awaited_once()
    assert capability_issues.record_issue.await_args.kwargs["error_message"] == (
        private_sentinel
    )
    public_payload = json.dumps(
        {
            "transition": transition.kwargs,
            "event": emitted_event.__dict__,
            "delivery": delivery.send_error.await_args.kwargs,
            "result": result.__dict__,
        },
        default=str,
    )
    assert private_sentinel not in public_payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extend_info", "message_text", "task_content", "expected_public_label"),
    [
        (
            {"public_task_label": "Public label from extend_info"},
            "PRIVATE_SENTINEL_message_text_explicit_label",
            "INTERNAL DISPATCH TASK PRIVATE_SENTINEL_task_content",
            "Public label from extend_info",
        ),
        (
            None,
            "PRIVATE_SENTINEL_message_text_public_task_content",
            "Requesting Insurer",
            "Requesting Insurer",
        ),
        (
            None,
            "PRIVATE_SENTINEL_message_text_generic_label",
            "INTERNAL DISPATCH TASK PRIVATE_SENTINEL_task_content",
            "Requesting Insurer",
        ),
        (
            None,
            "PRIVATE_SENTINEL_message_text_unmarked_task",
            "Review the private underwriting instructions",
            "Requesting Insurer",
        ),
        (
            None,
            "   ",
            "INTERNAL DISPATCH TASK PRIVATE_SENTINEL_task_content",
            "Requesting Insurer",
        ),
    ],
)
async def test_task_tracking_uses_public_label_policy_without_leaking_private_task(
    extend_info,
    message_text,
    task_content,
    expected_public_label,
):
    prepared_private = "INTERNAL DISPATCH TASK prepared private prompt"
    delivery = MagicMock()
    delivery.send_task_submitted = AsyncMock()
    delivery.send_task_update = AsyncMock()
    transport = DirectTransport(
        response_handler=MagicMock(),
        tsm=MagicMock(),
        a2a_transport=MagicMock(
            create_task_for_tracking=AsyncMock(
                return_value={"created_at": "2026-01-01T00:00:00Z"}
            )
        ),
        remote_task_reader=MagicMock(),
        delivery=delivery,
        message_reader=MagicMock(),
        artifact_store=MagicMock(),
        task_updater=MagicMock(),
        object_storage=MagicMock(),
    )
    message = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-msg-1",
        related_message_id="user-msg-1",
        agent_id="agent-1",
        message_content=MessageContent(message_text=message_text),
        task_content=task_content,
        client_request_id="client-1",
        extend_info=extend_info,
    )
    agent_card = MagicMock()
    agent_card.name = "Insurer"

    await transport._setup_task_tracking(
        message,
        agent_card,
        Message(
            role=MessageRole.USER,
            parts=[TextPart(kind="text", text=prepared_private)],
        ),
        "room-1",
    )

    submitted_kwargs = delivery.send_task_submitted.await_args.kwargs
    update_kwargs = delivery.send_task_update.await_args.kwargs
    assert submitted_kwargs["task_content"] == expected_public_label
    assert update_kwargs["status_message"] == expected_public_label
    delivered_payload = f"{submitted_kwargs} {update_kwargs}"
    assert prepared_private not in delivered_payload
    assert "PRIVATE_SENTINEL" not in delivered_payload
    assert "INTERNAL DISPATCH TASK" not in delivered_payload


class TestHandleSyncResponseSuccess:
    """handle_sync_response returns extracted content for a message-type response."""

    @pytest.mark.asyncio
    async def test_handle_sync_response_success(self):
        proc = _make_processor()

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        prepared_message = MagicMock()

        task_info = {
            "webhook_token": "tok-123",
            "context_id": "ctx-1",
            "created_at": "2025-01-01T00:00:00Z",
        }
        proc._setup_tracking_context = AsyncMock(
            return_value=(
                task_info,
                MagicMock(
                    room_id="room-1",
                    current_message=current_message,
                    agent_card=agent_card,
                    user_message_id="msg-1",
                    task_info=task_info,
                    send_sse=False,
                ),
            )
        )

        # Agent returns a message-type response dict
        proc.a2a_transport.send_message_to_tracked_agent = AsyncMock(
            return_value={"type": "message", "content": "Hello from agent"}
        )
        proc.tsm.transition_task = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc.response_handler.handle = AsyncMock()

        success, text, paused, agent_task_id = await proc.handle_sync_response(
            current_message=current_message,
            agent_card=agent_card,
            prepared_message=prepared_message,
            room_id="room-1",
            _user_id="user-1",
            user_message_id="msg-1",
        )

        assert success is True
        assert text == "Hello from agent"
        assert paused is None
        proc.tsm.transition_task.assert_awaited_once()
        assert proc.tsm.transition_task.call_args[0][1] == TaskState.completed


class TestHandleSyncResponseWithPolling:
    """When a sync response returns a task in working state with a non-push agent,
    _poll_task_until_complete is invoked."""

    @pytest.mark.asyncio
    async def test_handle_sync_response_with_polling(self):
        proc = _make_processor()

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        prepared_message = MagicMock()

        task_info = {
            "webhook_token": "tok-123",
            "context_id": "ctx-1",
            "created_at": "2025-01-01T00:00:00Z",
        }
        ctx_mock = MagicMock(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info=task_info,
            send_sse=False,
        )
        proc._setup_tracking_context = AsyncMock(return_value=(task_info, ctx_mock))

        # Agent returns a task-type response in working state
        proc.a2a_transport.send_message_to_tracked_agent = AsyncMock(
            return_value={
                "type": "task",
                "task_id": "remote-task-42",
                "status": TaskState.working,
            }
        )
        proc.a2a_transport.has_push_notification_capability = MagicMock(
            return_value=False
        )
        proc.tsm.transition_task = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc.tsm.notify_task = AsyncMock()

        completed_task = MagicMock()
        completed_task.status.state = TaskState.completed
        completed_task.artifacts = None
        completed_task.model_dump = MagicMock(return_value={})

        proc._poll_task_until_complete = AsyncMock(return_value=completed_task)
        proc._finalize_polled_task = AsyncMock(return_value=(True, "done", None, None))
        proc.response_handler.handle = AsyncMock()

        success, text, paused, agent_task_id = await proc.handle_sync_response(
            current_message=current_message,
            agent_card=agent_card,
            prepared_message=prepared_message,
            room_id="room-1",
            _user_id="user-1",
            user_message_id="msg-1",
        )

        proc._poll_task_until_complete.assert_awaited_once()
        call_args = proc._poll_task_until_complete.call_args
        assert call_args.kwargs["task_id"] == "remote-task-42"
        assert success is True


class TestPollTaskUntilCompleteReachesTerminal:
    """_poll_task_until_complete keeps polling until a terminal state is returned."""

    @pytest.mark.asyncio
    async def test_poll_task_until_complete_reaches_terminal(self, monkeypatch):
        proc = _make_processor()
        agent_card = MagicMock()

        working_task = MagicMock()
        working_task.status.state = TaskState.working

        completed_task = MagicMock()
        completed_task.status.state = TaskState.completed

        fetch_remote_task = AsyncMock(
            side_effect=[working_task, working_task, completed_task]
        )
        monkeypatch.setattr(direct_module, "fetch_remote_task", fetch_remote_task)

        result = await proc._poll_task_until_complete(
            agent_card=agent_card,
            task_id="remote-task-1",
            message_id="msg-1",
            timeout_seconds=30,
            initial_delay=0.01,
            max_delay=0.02,
        )

        assert result is not None
        assert result.status.state == TaskState.completed
        assert fetch_remote_task.await_count == 3


class TestHandleStreamingCancellation:
    """When the cancellation token is set during streaming, the processor
    transitions to canceled and attempts remote task cancellation."""

    @pytest.mark.asyncio
    async def test_handle_streaming_cancellation(self):
        proc = _make_processor()
        proc.tsm.transition_task = AsyncMock()
        proc._try_cancel_remote_task = AsyncMock()

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"

        task_info = {"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"}
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info=task_info,
        )
        streaming_state = MagicMock()
        streaming_state.full_response_text = "partial text"
        proc.response_handler.handle = AsyncMock()

        status, text = await proc._handle_streaming_cancellation(ctx, streaming_state)

        assert status == ProcessingStatus.CANCELED
        assert text == "partial text"
        proc.tsm.transition_task.assert_awaited_once()
        assert proc.tsm.transition_task.call_args[0][1] == TaskState.canceled
        proc._try_cancel_remote_task.assert_awaited_once_with(
            current_message, agent_card
        )
        proc.response_handler.handle.assert_awaited_once()


class TestHandleStreamStatusUpdatePrivacy:
    @pytest.mark.asyncio
    async def test_interactive_status_update_emits_public_label_not_remote_prompt(self):
        private_prompt = "PRIVATE_SENTINEL_streaming_interactive_prompt"
        public_label = "Requesting Claims Agent"
        proc = _make_processor()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc.tsm.notify_task = AsyncMock()
        current_message = _make_room_agent_message(
            extend_info={"public_task_label": public_label}
        )
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "Claims Agent"
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            send_sse=True,
        )
        result = MagicMock(
            status=TaskStatus(
                state=TaskState.input_required,
                message=Message(
                    role=MessageRole.AGENT,
                    parts=[Part(root=TextPart(text=private_prompt))],
                    message_id="remote-status-message",
                ),
            ),
            final=False,
        )

        await proc._handle_stream_status_update(
            result,
            ctx,
            MessageStreamingState(),
        )

        proc.tsm.notify_task.assert_awaited_once()
        notify_kwargs = proc.tsm.notify_task.await_args.kwargs
        assert notify_kwargs["status_message"] == public_label
        assert private_prompt not in repr(notify_kwargs)
        persisted_task = current_message.message_content.message_task
        assert persisted_task.status.message is None


class TestFinalizeStreamingWritesArtifacts:
    """_finalize_streaming transitions to completed and calls _emit_terminal
    with the accumulated content."""

    @pytest.mark.asyncio
    async def test_finalize_streaming_writes_artifacts(self):
        proc = _make_processor()
        proc.tsm.transition_task = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"

        task_info = {"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"}
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info=task_info,
            send_sse=False,
        )

        streaming_state = MessageStreamingState()
        streaming_state.full_response_text = "Final answer from agent."
        streaming_state.accumulated_parts = [MagicMock(), MagicMock()]
        streaming_state.non_text_parts = []
        proc.response_handler.handle = AsyncMock()

        status, text = await proc._finalize_streaming(ctx, streaming_state)

        assert status == ProcessingStatus.SUCCESS
        assert text == "Final answer from agent."
        proc.tsm.transition_task.assert_awaited_once()
        assert proc.tsm.transition_task.call_args[0][1] == TaskState.completed
        proc.response_handler.handle.assert_awaited_once()
        event_arg = proc.response_handler.handle.call_args[0][0]
        assert event_arg.message_id == "msg-1"
        assert event_arg.room_id == "room-1"
        assert event_arg.text == "Final answer from agent."
        persisted_task = current_message.message_content.message_task
        assert persisted_task.history is None
        assert persisted_task.artifacts is not None
        assert persisted_task.artifacts[0].name == "response"
        assert (
            persisted_task.artifacts[0].parts[0].root.text == "Final answer from agent."
        )

    @pytest.mark.asyncio
    async def test_already_failed_task_persists_only_public_failure_text(self):
        private_failure = "PRIVATE_SENTINEL_remote_failure_detail"
        proc = _make_processor()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc._emit_terminal = AsyncMock()

        task = Task(
            id="task-001",
            status=TaskStatus(state=TaskState.failed),
        )
        current_message = _make_room_agent_message(
            message_content=MessageContent(
                message_text="",
                message_task=task,
            )
        )
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            send_sse=False,
        )
        streaming_state = MessageStreamingState(
            full_response_text=private_failure,
        )

        status, text = await proc._finalize_streaming(ctx, streaming_state)

        assert status == ProcessingStatus.FAILED
        assert text == "Task failed"
        assert current_message.message_content.message_text == "Task failed"
        assert private_failure not in current_message.model_dump_json()
        proc.tsm.persist_message.assert_awaited_once_with(current_message)


class TestDispatchTerminalNotificationFailure:
    @pytest.mark.asyncio
    async def test_streaming_terminal_notification_failure_does_not_mark_task_failed(
        self,
    ):
        proc = _make_processor()
        message = _make_room_agent_message()
        proc.a2a_transport.has_streaming_capability = MagicMock(return_value=True)
        proc.capability_issue_service = None
        proc.tsm.transition_task = AsyncMock()

        handler_store = MagicMock()
        handler_store.update_task_state_on_message = AsyncMock(
            return_value=(True, None)
        )
        handler_store.accumulate_artifact_on_message = AsyncMock(return_value=True)
        handler_store.get_pending_continuation_on_message = AsyncMock(return_value=None)
        delivery = MagicMock()
        rmc = MagicMock()
        rmc.resume_queue_from_continuation = AsyncMock(return_value=True)
        task_notifier = MagicMock()
        task_notification_impl = AsyncMock(
            side_effect=RuntimeError("notification store missing read")
        )
        proc.response_handler = AgentResponseHandler(
            message_writer=handler_store,
            task_writer=handler_store,
            continuation_store=handler_store,
            client_request_resolver=handler_store,
            room_reader=handler_store,
            hitl_reader=handler_store,
            delivery=delivery,
            room_message_center=rmc,
            task_notifier=task_notifier,
            task_notification_store=MagicMock(),
            task_notification_impl=task_notification_impl,
        )
        proc._message_reader.get_room_agent_message_by_message_id = AsyncMock(
            return_value=message
        )

        async def successful_streaming_with_terminal_notification_failure(
            *_args, **_kwargs
        ):
            message.message_content.message_text = "Final answer from agent."
            await proc.tsm.transition_task(
                message,
                CommonTaskState.COMPLETED,
                persist=True,
            )
            await proc.response_handler.handle(
                AgentEvent(
                    kind="response",
                    message_id=message.message_id,
                    room_id="room-1",
                    agent_id=message.agent_id or "",
                    text="Final answer from agent.",
                    related_message_id=message.related_message_id,
                    user_id=message.user_id or "",
                    skip_persist=True,
                )
            )
            return ProcessingStatus.SUCCESS, "Final answer from agent."

        proc.handle_streaming_response = AsyncMock(
            side_effect=successful_streaming_with_terminal_notification_failure
        )
        proc._emit_terminal = AsyncMock()

        agent = MagicMock()
        agent.agent_card = MagicMock()
        ctx = DispatchContext(
            agent=agent,
            room_agent_message=message,
            room_id="room-1",
            user_message_id="user-msg-1",
            prepared_message=MagicMock(),
        )

        result = await proc.dispatch(ctx, message)

        assert result.status == ProcessingStatus.SUCCESS
        assert result.response_text == "Final answer from agent."
        transition_states = [
            call.args[1] for call in proc.tsm.transition_task.await_args_list
        ]
        assert CommonTaskState.COMPLETED in transition_states
        assert CommonTaskState.FAILED not in transition_states
        proc._emit_terminal.assert_not_awaited()


# =============================================================================
# Phase 1: artifact_update streaming tests
# =============================================================================


class TestMessageChunkEmitsArtifactUpdate:
    """_handle_stream_message_chunk keeps nonterminal content in memory only."""

    @pytest.mark.asyncio
    async def test_message_chunk_keeps_text_in_memory_without_public_sse(
        self, monkeypatch
    ):
        proc = _make_processor()
        proc.response_handler.handle = AsyncMock()
        proc.delivery.send_artifact_update = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"

        task_info = {"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"}
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info=task_info,
            send_sse=True,
        )

        streaming_state = MessageStreamingState()

        text_part = {"text": "Hello"}

        result = MagicMock()
        result.parts = [text_part]
        result.role = MessageRole.AGENT
        result.message_id = "a2a-msg-1"

        monkeypatch.setattr(
            "common.utils.a2a_helpers.extract_parts",
            lambda parts: MagicMock(
                text="Hello", has_non_text=False, file_parts=[], data_parts=[]
            ),
        )

        await proc._handle_stream_message_chunk(result, ctx, streaming_state)

        monkeypatch.undo()

        assert streaming_state.full_response_text == "Hello"
        assert streaming_state.accumulated_parts
        proc.delivery.send_artifact_update.assert_not_awaited()
        proc.response_handler.handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_message_chunk_skips_empty_content(self, monkeypatch):
        """Empty text content should not emit any SSE event."""
        proc = _make_processor()
        proc.delivery.send_artifact_update = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"

        task_info = {"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"}
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info=task_info,
            send_sse=True,
        )

        streaming_state = MessageStreamingState()

        result = MagicMock()
        result.parts = []
        result.role = MessageRole.AGENT
        result.message_id = "a2a-msg-1"

        monkeypatch.setattr(
            "common.utils.a2a_helpers.extract_parts",
            lambda parts: MagicMock(
                text="", has_non_text=False, file_parts=[], data_parts=[]
            ),
        )

        await proc._handle_stream_message_chunk(result, ctx, streaming_state)

        monkeypatch.undo()

        proc.delivery.send_artifact_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_role_message_chunk_is_ignored_before_persistence_or_sse():
    private_sentinel = "PRIVATE_SENTINEL_user_stream_chunk"
    proc = _make_processor()
    proc.delivery.send_artifact_update = AsyncMock()
    proc.tsm.persist_message = AsyncMock(return_value=True)

    current_message = _make_room_agent_message()
    original_task = current_message.message_content.message_task.model_dump(mode="json")
    agent_card = MagicMock(spec_set=["name"])
    agent_card.name = "test-agent"
    ctx = ProcessingContext(
        room_id="room-1",
        current_message=current_message,
        agent_card=agent_card,
        user_message_id="msg-1",
        task_info={"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"},
        send_sse=True,
    )
    streaming_state = MessageStreamingState()
    result = Message(
        role=MessageRole.USER,
        message_id="user-stream-message",
        parts=[Part(root=TextPart(text=private_sentinel))],
    )

    await proc._handle_stream_message_chunk(result, ctx, streaming_state)

    assert streaming_state == MessageStreamingState()
    assert (
        current_message.message_content.message_task.model_dump(mode="json")
        == original_task
    )
    proc.tsm.persist_message.assert_not_awaited()
    proc.delivery.send_artifact_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_working_stream_message_chunk_does_not_persist_remote_metadata_or_history():
    private_sentinel = "PRIVATE_SENTINEL_stream_message_part_metadata"
    proc = _make_processor()
    proc.delivery.send_artifact_update = AsyncMock()
    proc.tsm.persist_message = AsyncMock(return_value=True)
    current_message = _make_room_agent_message()
    agent_card = MagicMock(spec_set=["name"])
    agent_card.name = "test-agent"
    ctx = ProcessingContext(
        room_id="room-1",
        current_message=current_message,
        agent_card=agent_card,
        user_message_id="msg-1",
        task_info={"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"},
        send_sse=False,
    )
    result = Message(
        role=MessageRole.AGENT,
        message_id="remote-agent-message",
        parts=[
            Part(
                root=TextPart(
                    text="Visible streaming text",
                    metadata={"private": private_sentinel},
                )
            )
        ],
        metadata={"private": private_sentinel},
    )

    await proc._handle_stream_message_chunk(result, ctx, MessageStreamingState())

    persisted_message = proc.tsm.persist_message.await_args.args[0]
    persisted_task = persisted_message.message_content.message_task
    assert persisted_task.history in (None, [])
    assert private_sentinel not in persisted_task.model_dump_json()


class TestArtifactUpdateRoutedThroughHandler:
    """_handle_stream_artifact_update keeps nonterminal artifacts in memory only."""

    @pytest.mark.asyncio
    async def test_artifact_chunk_waits_for_finalization_even_when_sse_enabled(self):
        proc = _make_processor()
        proc.response_handler.handle = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc.delivery.send_artifact_update = AsyncMock()

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"

        task_info = {"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"}
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info=task_info,
            send_sse=True,
        )

        streaming_state = MessageStreamingState()

        artifact = MagicMock()
        artifact.artifact_id = "art-1"
        artifact.parts = []
        artifact.model_dump = MagicMock(
            return_value={"artifact_id": "art-1", "parts": []}
        )

        result = MagicMock()
        result.artifact = artifact
        result.append = True
        result.last_chunk = False

        proc._convert_inline_bytes_to_s3 = AsyncMock()

        await proc._handle_stream_artifact_update(result, ctx, streaming_state)

        proc.response_handler.handle.assert_not_awaited()
        proc.tsm.persist_message.assert_not_awaited()
        proc.delivery.send_artifact_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_artifact_text_uses_append_semantics_for_finalization(self):
        proc = _make_processor()
        proc.response_handler.handle = AsyncMock()
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info={"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"},
            send_sse=True,
        )
        streaming_state = MessageStreamingState(full_response_text="stale")
        initial = MagicMock(
            artifact=Artifact(
                artifact_id="artifact-1",
                parts=[Part(root=TextPart(text="Hello"))],
            ),
            append=False,
            last_chunk=False,
        )
        appended = MagicMock(
            artifact=Artifact(
                artifact_id="artifact-1",
                parts=[Part(root=TextPart(text=" world"))],
            ),
            append=True,
            last_chunk=True,
        )

        await proc._handle_stream_artifact_update(initial, ctx, streaming_state)
        await proc._handle_stream_artifact_update(appended, ctx, streaming_state)

        assert streaming_state.full_response_text == "Hello world"
        proc.response_handler.handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_artifact_chunk_with_last_chunk_waits_for_terminal_finalization(self):
        private_sentinel = "PRIVATE_SENTINEL_stream_artifact_metadata"
        proc = _make_processor()
        proc.response_handler.handle = AsyncMock()
        proc._convert_inline_bytes_to_s3 = AsyncMock()
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info={"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"},
            send_sse=True,
        )
        artifact = Artifact(
            artifact_id="artifact-1",
            name="file-result",
            parts=[
                Part(
                    root=FilePart(
                        file=FileContent(
                            uri="https://storage.example/result.csv",
                            mimeType="text/csv",
                            name="result.csv",
                        ),
                        metadata={
                            "s3_key": "artifacts/room/msg/result.csv",
                            "request": private_sentinel,
                        },
                    )
                )
            ],
            metadata={"request": private_sentinel},
        )
        result = MagicMock(artifact=artifact, append=False, last_chunk=True)
        streaming_state = MessageStreamingState()

        await proc._handle_stream_artifact_update(result, ctx, streaming_state)

        proc.response_handler.handle.assert_not_awaited()
        assert streaming_state.non_text_parts
        retained_payload = json.dumps(streaming_state.non_text_parts, sort_keys=True)
        assert "https://storage.example/result.csv" in retained_payload

    @pytest.mark.asyncio
    async def test_artifact_chunk_with_private_bytes_never_reaches_public_handler(self):
        private_bytes = "PRIVATE_SENTINEL_direct_stream_bytes"
        private_metadata = "PRIVATE_SENTINEL_direct_stream_metadata"
        proc = _make_processor()
        proc.response_handler.handle = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc._artifact_store.accumulate_artifact_on_message = AsyncMock(
            return_value=True
        )
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info={"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"},
            send_sse=True,
        )
        streaming_state = MessageStreamingState()
        artifact = Artifact(
            artifact_id="artifact-1",
            name="partial-file",
            parts=[
                Part(
                    root=FilePart(
                        file=FileContent(
                            bytes=private_bytes,
                            mimeType="text/plain",
                            name="private.txt",
                        ),
                        metadata={"private": private_metadata},
                    )
                )
            ],
            metadata={"private": private_metadata},
        )
        result = MagicMock(artifact=artifact, append=False, last_chunk=False)
        proc._convert_inline_bytes_to_s3 = AsyncMock(
            side_effect=RuntimeError("S3 unavailable")
        )

        await proc._handle_stream_artifact_update(result, ctx, streaming_state)

        proc.tsm.persist_message.assert_not_awaited()
        proc._artifact_store.accumulate_artifact_on_message.assert_not_awaited()
        proc.response_handler.handle.assert_not_awaited()
        task = current_message.message_content.message_task
        assert task.artifacts in (None, [])
        assert streaming_state.non_text_parts
        assert private_bytes in json.dumps(streaming_state.non_text_parts)

    @pytest.mark.asyncio
    async def test_later_empty_artifact_chunk_does_not_erase_accumulated_final_parts(
        self,
    ):
        proc = _make_processor()
        proc.response_handler.handle = AsyncMock()
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info={"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"},
            send_sse=True,
        )
        streaming_state = MessageStreamingState()
        proc._convert_inline_bytes_to_s3 = AsyncMock()
        first = MagicMock(
            artifact=Artifact(
                artifact_id="artifact-1",
                parts=[
                    Part(
                        root=FilePart(
                            file=FileContent(
                                uri="https://storage.example/result.txt",
                                mimeType="text/plain",
                                name="result.txt",
                            )
                        )
                    )
                ],
            ),
            append=False,
            last_chunk=False,
        )
        second = MagicMock(
            artifact=Artifact(artifact_id="artifact-1", parts=[]),
            append=True,
            last_chunk=True,
        )

        await proc._handle_stream_artifact_update(first, ctx, streaming_state)
        await proc._handle_stream_artifact_update(second, ctx, streaming_state)

        assert len(streaming_state.non_text_parts) == 1
        retained = streaming_state.non_text_parts[0]
        assert retained["kind"] == "file"
        assert retained["file"]["uri"] == "https://storage.example/result.txt"
        assert retained["file"]["name"] == "result.txt"

    @pytest.mark.asyncio
    async def test_artifact_chunk_no_sse_waits_for_finalization(self):
        """When send_sse=False, mid-stream artifacts are retained only in memory."""
        proc = _make_processor()
        proc.response_handler.handle = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc._artifact_store.accumulate_artifact_on_message = AsyncMock(
            return_value=True
        )

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"

        task_info = {"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"}
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info=task_info,
            send_sse=False,
        )

        streaming_state = MessageStreamingState()

        artifact = MagicMock()
        artifact.artifact_id = "art-1"
        artifact.parts = []
        artifact.model_dump = MagicMock(
            return_value={"artifact_id": "art-1", "parts": []}
        )

        result = MagicMock()
        result.artifact = artifact
        result.append = False
        result.last_chunk = False

        proc._convert_inline_bytes_to_s3 = AsyncMock()

        await proc._handle_stream_artifact_update(result, ctx, streaming_state)

        # Handler NOT called (no SSE)
        proc.response_handler.handle.assert_not_awaited()
        # Old persist_message NOT called
        proc.tsm.persist_message.assert_not_awaited()
        proc._artifact_store.accumulate_artifact_on_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_artifact_chunk_no_sse_does_not_persist_midstream(self):
        """No-SSE direct streaming still waits until completed finalization to persist."""
        proc = _make_processor()
        proc.response_handler.handle = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc._artifact_store.accumulate_artifact_on_message = AsyncMock(
            return_value=True
        )
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info={"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"},
            send_sse=False,
        )
        streaming_state = MessageStreamingState()
        artifact = Artifact(
            artifact_id="artifact-1",
            parts=[
                Part(
                    root=FilePart(
                        file=FileContent(
                            uri="https://storage.example/result.txt",
                            mimeType="text/plain",
                            name="result.txt",
                        )
                    )
                )
            ],
        )
        result = MagicMock(artifact=artifact, append=False, last_chunk=False)
        proc._convert_inline_bytes_to_s3 = AsyncMock()

        await proc._handle_stream_artifact_update(result, ctx, streaming_state)

        proc.response_handler.handle.assert_not_awaited()
        proc.tsm.persist_message.assert_not_awaited()
        proc._artifact_store.accumulate_artifact_on_message.assert_not_awaited()
        assert streaming_state.non_text_parts


class TestFinalizeStreamingArtifactDelivery:
    """_finalize_streaming relies on the terminal task update for delivery."""

    @pytest.mark.asyncio
    async def test_finalize_does_not_emit_empty_artifact_sentinel(self):
        proc = _make_processor()
        proc.delivery.send_artifact_update = AsyncMock()
        proc.tsm.transition_task = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc.response_handler.handle = AsyncMock()

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"

        task_info = {"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"}
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info=task_info,
            send_sse=True,
        )

        streaming_state = MessageStreamingState()
        streaming_state.full_response_text = "Agent done."
        streaming_state.accumulated_parts = [MagicMock()]
        streaming_state.non_text_parts = []

        status, text = await proc._finalize_streaming(ctx, streaming_state)

        assert status == ProcessingStatus.SUCCESS
        assert text == "Agent done."
        proc.delivery.send_artifact_update.assert_not_awaited()


# =============================================================================
# _process_sync_response: persist gating on tracked vs degraded path
# =============================================================================


class TestProcessSyncResponsePersistGating:
    """Tracked path skips full-document persist to avoid overwriting
    the real task saved by A2A transport partial $set."""

    def _make_ctx(self, current_message, agent_card, task_info):
        return ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info=task_info,
            send_sse=False,
        )

    @pytest.mark.asyncio
    async def test_tracked_path_with_persisted_skips_persist(self):
        """When task_info is set and response has persisted=True,
        transition_task must be called with persist=False."""
        proc = _make_processor()
        proc.tsm.transition_task = AsyncMock()
        proc.response_handler.handle = AsyncMock()

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        task_info = {"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"}
        ctx = self._make_ctx(current_message, agent_card, task_info)

        response = {
            "type": "message",
            "content": "Agent result with artifacts",
            "persisted": True,
        }

        await proc._process_sync_response(
            response=response,
            current_message=current_message,
            agent_card=agent_card,
            room_id="room-1",
            message_id="msg-1",
            task_info=task_info,
            ctx=ctx,
            token=None,
        )

        proc.tsm.transition_task.assert_awaited_once()
        call_kwargs = proc.tsm.transition_task.call_args[1]
        assert call_kwargs["persist"] is False

    @pytest.mark.asyncio
    async def test_tracked_path_with_persisted_false_falls_back_to_persist(self):
        """When task_info is set but persisted=False (DB write not confirmed),
        fall back to full persist so the terminal state is not lost."""
        proc = _make_processor()
        proc.tsm.transition_task = AsyncMock()
        proc.response_handler.handle = AsyncMock()

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        task_info = {"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"}
        ctx = self._make_ctx(current_message, agent_card, task_info)

        response = {
            "type": "message",
            "content": "Agent result",
            "persisted": False,
        }

        await proc._process_sync_response(
            response=response,
            current_message=current_message,
            agent_card=agent_card,
            room_id="room-1",
            message_id="msg-1",
            task_info=task_info,
            ctx=ctx,
            token=None,
        )

        proc.tsm.transition_task.assert_awaited_once()
        call_kwargs = proc.tsm.transition_task.call_args[1]
        assert call_kwargs["persist"] is True
        artifacts = current_message.message_content.message_task.artifacts
        assert artifacts is not None
        assert artifacts[0].parts[0].root.text == "Agent result"

    @pytest.mark.asyncio
    async def test_degraded_path_persists(self):
        """When task_info is None (degraded/fallback path), persist=True."""
        proc = _make_processor()
        proc.tsm.transition_task = AsyncMock()
        proc.delivery.send_task_update = AsyncMock()
        proc.response_handler.handle = AsyncMock()

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        ctx = self._make_ctx(current_message, agent_card, task_info=None)

        response = {
            "type": "message",
            "content": "Fallback response",
        }

        await proc._process_sync_response(
            response=response,
            current_message=current_message,
            agent_card=agent_card,
            room_id="room-1",
            message_id="msg-1",
            task_info=None,
            ctx=ctx,
            token=None,
        )

        proc.tsm.transition_task.assert_awaited_once()
        call_kwargs = proc.tsm.transition_task.call_args[1]
        assert call_kwargs["persist"] is True
        artifacts = current_message.message_content.message_task.artifacts
        assert artifacts is not None
        assert artifacts[0].parts[0].root.text == "Fallback response"


class TestProcessSyncResponseRespectsStatus:
    """When the agent returns a non-completed terminal state (e.g. failed),
    _process_sync_response must use that state instead of hardcoding completed."""

    def _make_ctx(self, current_message, agent_card, task_info):
        return ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info=task_info,
            send_sse=False,
        )

    @pytest.mark.asyncio
    async def test_failed_status_uses_failed_state(self):
        """Response with status=failed should transition to TaskState.failed,
        not TaskState.completed."""
        private_sentinel = "PRIVATE_SENTINEL_normalized_sync_error"
        proc = _make_processor()
        proc.tsm.transition_task = AsyncMock()
        proc.response_handler.handle = AsyncMock()

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        task_info = {"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"}
        ctx = self._make_ctx(current_message, agent_card, task_info)

        response = {
            "type": "message",
            "content": None,
            "status": "failed",
            "error": private_sentinel,
            "persisted": True,
        }

        success, text, _, agent_task_id = await proc._process_sync_response(
            response=response,
            current_message=current_message,
            agent_card=agent_card,
            room_id="room-1",
            message_id="msg-1",
            task_info=task_info,
            ctx=ctx,
            token=None,
        )

        # transition_task should use TaskState.failed
        call_args = proc.tsm.transition_task.call_args
        assert call_args[0][1] == TaskState.failed

        # _emit_terminal should receive state=failed and error text
        handle_call = proc.response_handler.handle.call_args[0][0]
        assert handle_call.kind == "error"
        assert handle_call.error_text == "Agent processing failed"

        # Public results must not echo the remote error payload.
        assert text == "Agent processing failed"
        assert private_sentinel not in json.dumps(handle_call.__dict__)

        # Failed dispatch must return success=False
        assert success is False

    @pytest.mark.asyncio
    async def test_no_status_defaults_to_completed(self):
        """Response without status field (Message kind) should default to completed."""
        proc = _make_processor()
        proc.tsm.transition_task = AsyncMock()
        proc.response_handler.handle = AsyncMock()

        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        task_info = {"webhook_token": "tok", "context_id": "ctx", "created_at": "t0"}
        ctx = self._make_ctx(current_message, agent_card, task_info)

        response = {
            "type": "message",
            "content": "Message result",
            "persisted": True,
        }

        success, text, _, agent_task_id = await proc._process_sync_response(
            response=response,
            current_message=current_message,
            agent_card=agent_card,
            room_id="room-1",
            message_id="msg-1",
            task_info=task_info,
            ctx=ctx,
            token=None,
        )

        call_args = proc.tsm.transition_task.call_args
        assert call_args[0][1] == TaskState.completed
        assert success is True


class TestHandleSyncResponseInteractive:
    """handle_sync_response returns agent_task_id for input_required responses."""

    @pytest.mark.asyncio
    async def test_input_required_returns_agent_task_id(self):
        proc = _make_processor()
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        prepared_message = MagicMock()

        task_info = {
            "webhook_token": "tok-123",
            "context_id": "ctx-1",
            "created_at": "2025-01-01T00:00:00Z",
        }
        proc._setup_tracking_context = AsyncMock(
            return_value=(
                task_info,
                MagicMock(
                    room_id="room-1",
                    current_message=current_message,
                    agent_card=agent_card,
                    user_message_id="msg-1",
                    task_info=task_info,
                    send_sse=False,
                ),
            )
        )
        proc.a2a_transport.send_message_to_tracked_agent = AsyncMock(
            return_value={
                "type": "task",
                "status": "input-required",
                "requires_input": True,
                "task_id": "real-agent-task-abc123",
                "message": "Please approve.",
            }
        )
        proc.tsm.notify_task = AsyncMock()

        success, text, paused, agent_task_id = await proc.handle_sync_response(
            current_message=current_message,
            agent_card=agent_card,
            prepared_message=prepared_message,
            room_id="room-1",
            _user_id="user-1",
            user_message_id="msg-1",
        )

        assert success is True
        assert text is None
        assert paused == current_message.message_id
        assert agent_task_id == "real-agent-task-abc123"
        notify_kwargs = proc.tsm.notify_task.await_args.kwargs
        assert notify_kwargs["status_message"] == "Please approve."
        assert notify_kwargs["status_message"] == "Please approve."

    @pytest.mark.asyncio
    async def test_no_task_tracking_interactive_does_not_persist_remote_prompt(self):
        private_prompt = "PRIVATE_SENTINEL_sync_interactive_prompt"
        proc = _make_processor()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc.tsm.notify_task = AsyncMock()
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name", "url"])
        agent_card.name = "test-agent"
        agent_card.url = "https://agent.example"
        ctx = ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="msg-1",
            task_info=None,
            send_sse=False,
        )

        success, text, paused, agent_task_id = await proc._process_sync_response(
            response={
                "type": "task",
                "status": "input-required",
                "requires_input": True,
                "task_id": "remote-task-1",
                "message": private_prompt,
            },
            current_message=current_message,
            agent_card=agent_card,
            room_id="room-1",
            message_id="msg-1",
            task_info=None,
            ctx=ctx,
            token=None,
        )

        assert success is True
        assert text is None
        assert paused == current_message.message_id
        assert agent_task_id == "remote-task-1"
        persisted_message = proc.tsm.persist_message.await_args.args[0]
        task = persisted_message.message_content.message_task
        assert task.status.state == CommonTaskState.INPUT_REQUIRED
        assert task.status.message is None
        assert private_prompt not in task.model_dump_json()

    @pytest.mark.asyncio
    async def test_input_required_keeps_prompt_internal_not_task_status_message(self):
        """Remote prompts stay internal so dispatch() can build the HITL prompt."""
        proc = _make_processor()
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"

        task_info = {
            "webhook_token": "tok-123",
            "context_id": "ctx-1",
            "created_at": "2025-01-01T00:00:00Z",
        }
        proc._setup_tracking_context = AsyncMock(
            return_value=(
                task_info,
                MagicMock(
                    room_id="room-1",
                    current_message=current_message,
                    agent_card=agent_card,
                    user_message_id="msg-1",
                    task_info=task_info,
                    send_sse=False,
                ),
            )
        )
        proc.a2a_transport.send_message_to_tracked_agent = AsyncMock(
            return_value={
                "type": "task",
                "status": "input-required",
                "requires_input": True,
                "task_id": "task-abc",
                "message": "Please provide your API key.",
            }
        )
        proc.tsm.notify_task = AsyncMock()

        await proc.handle_sync_response(
            current_message=current_message,
            agent_card=agent_card,
            prepared_message=MagicMock(),
            room_id="room-1",
            _user_id="user-1",
            user_message_id="msg-1",
        )

        task = current_message.message_content.message_task
        assert task is not None
        assert task.status.message is None
        assert not hasattr(proc, "_internal_interactive_status_messages")

    @pytest.mark.asyncio
    async def test_input_required_without_message_leaves_task_status_message_none(self):
        """When requires_input has no message text, task.status.message stays None."""
        proc = _make_processor()
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"

        task_info = {
            "webhook_token": "tok-123",
            "context_id": "ctx-1",
            "created_at": "2025-01-01T00:00:00Z",
        }
        proc._setup_tracking_context = AsyncMock(
            return_value=(
                task_info,
                MagicMock(
                    room_id="room-1",
                    current_message=current_message,
                    agent_card=agent_card,
                    user_message_id="msg-1",
                    task_info=task_info,
                    send_sse=False,
                ),
            )
        )
        proc.a2a_transport.send_message_to_tracked_agent = AsyncMock(
            return_value={
                "type": "task",
                "status": "input-required",
                "requires_input": True,
                "task_id": "task-abc",
            }
        )
        proc.tsm.notify_task = AsyncMock()

        await proc.handle_sync_response(
            current_message=current_message,
            agent_card=agent_card,
            prepared_message=MagicMock(),
            room_id="room-1",
            _user_id="user-1",
            user_message_id="msg-1",
        )

        task = current_message.message_content.message_task
        assert task is not None
        assert task.status.message is None

    @pytest.mark.asyncio
    async def test_degraded_input_required_persists_agent_url_for_hitl_reply(self):
        """When task tracking setup fails, HITL replies still need agent_url."""
        proc = _make_processor()
        current_message = _make_room_agent_message(agent_url=None)
        agent_card = MagicMock(spec_set=["name", "url"])
        agent_card.name = "test-agent"
        agent_card.url = "http://localhost:9060"

        proc._setup_tracking_context = AsyncMock(
            return_value=(
                None,
                MagicMock(
                    room_id="room-1",
                    current_message=current_message,
                    agent_card=agent_card,
                    user_message_id="msg-1",
                    task_info=None,
                    send_sse=False,
                ),
            )
        )
        proc.delivery.send_task_submitted = AsyncMock()
        proc.delivery.send_task_update = AsyncMock()
        proc.a2a_transport.send_message_sync = AsyncMock(
            return_value={
                "kind": "task",
                "result": {
                    "kind": "task",
                    "id": "real-agent-task-abc123",
                    "status": {"state": "input-required"},
                    "artifacts": [],
                },
                "error": None,
            }
        )
        proc.tsm.persist_message = AsyncMock(return_value=True)

        success, text, paused, agent_task_id = await proc.handle_sync_response(
            current_message=current_message,
            agent_card=agent_card,
            prepared_message=MagicMock(),
            room_id="room-1",
            _user_id="user-1",
            user_message_id="msg-1",
        )

        assert success is True
        assert text is None
        assert paused == current_message.message_id
        assert agent_task_id == "real-agent-task-abc123"
        assert current_message.agent_url == "http://localhost:9060"
        proc.tsm.persist_message.assert_awaited_once_with(current_message)

    @pytest.mark.asyncio
    async def test_degraded_submitted_delivery_uses_public_task_label(self):
        private_task = "PRIVATE_SENTINEL_degraded_sync_task_content"
        private_message = "PRIVATE_SENTINEL_degraded_sync_message_text"
        public_label = "Requesting public insurer quote"
        proc = _make_processor()
        current_message = _make_room_agent_message(
            agent_url=None,
            task_content=private_task,
            message_content=MessageContent(
                message_text=private_message,
                message_task=Task(
                    id="task-001",
                    contextId="ctx-001",
                    status=TaskStatus(state=TaskState.working),
                    kind="task",
                ),
            ),
            extend_info={"public_task_label": public_label},
        )
        agent_card = MagicMock(spec_set=["name", "url"])
        agent_card.name = "test-agent"
        agent_card.url = "http://localhost:9060"

        proc._setup_tracking_context = AsyncMock(
            return_value=(
                None,
                MagicMock(
                    room_id="room-1",
                    current_message=current_message,
                    agent_card=agent_card,
                    user_message_id="msg-1",
                    task_info=None,
                    send_sse=False,
                ),
            )
        )
        proc.delivery.send_task_submitted = AsyncMock()
        proc.delivery.send_task_update = AsyncMock()
        proc.a2a_transport.send_message_sync = AsyncMock(
            return_value={
                "kind": "message",
                "result": {
                    "kind": "message",
                    "role": "agent",
                    "messageId": "agent-msg-1",
                    "parts": [{"kind": "text", "text": "Done"}],
                },
                "error": None,
            }
        )
        proc.tsm.transition_task = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc.response_handler.handle = AsyncMock()

        await proc.handle_sync_response(
            current_message=current_message,
            agent_card=agent_card,
            prepared_message=MagicMock(),
            room_id="room-1",
            _user_id="user-1",
            user_message_id="msg-1",
        )

        submitted_kwargs = proc.delivery.send_task_submitted.await_args.kwargs
        assert submitted_kwargs["task_content"] == public_label
        submitted_payload = json.dumps(submitted_kwargs, default=str)
        assert private_task not in submitted_payload
        assert private_message not in submitted_payload

    @pytest.mark.asyncio
    async def test_degraded_input_required_keeps_status_message_internal(self):
        """Degraded sync fallback must not persist the A2A status.message prompt."""
        proc = _make_processor()
        current_message = _make_room_agent_message(agent_url=None)
        agent_card = MagicMock(spec_set=["name", "url"])
        agent_card.name = "test-agent"
        agent_card.url = "http://localhost:9060"

        proc._setup_tracking_context = AsyncMock(
            return_value=(
                None,
                MagicMock(
                    room_id="room-1",
                    current_message=current_message,
                    agent_card=agent_card,
                    user_message_id="msg-1",
                    task_info=None,
                    send_sse=False,
                ),
            )
        )
        proc.delivery.send_task_submitted = AsyncMock()
        proc.a2a_transport.send_message_sync = AsyncMock(
            return_value={
                "kind": "task",
                "result": {
                    "kind": "task",
                    "id": "real-agent-task-abc123",
                    "status": {
                        "state": "input-required",
                        "message": {
                            "kind": "message",
                            "role": "agent",
                            "messageId": "status-msg-1",
                            "parts": [
                                {
                                    "kind": "text",
                                    "text": "Which revenue period should I use?",
                                }
                            ],
                        },
                    },
                    "artifacts": [],
                },
                "error": None,
            }
        )
        proc.tsm.persist_message = AsyncMock(return_value=True)

        await proc.handle_sync_response(
            current_message=current_message,
            agent_card=agent_card,
            prepared_message=MagicMock(),
            room_id="room-1",
            _user_id="user-1",
            user_message_id="msg-1",
        )

        task = current_message.message_content.message_task
        assert task is not None
        assert task.status.message is None
        assert not hasattr(proc, "_internal_interactive_status_messages")

    @pytest.mark.asyncio
    async def test_requires_auth_without_status_sets_auth_required_state(self):
        """requires_auth=True without status must not leave task in working."""
        proc = _make_processor()
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"

        task_info = {
            "webhook_token": "tok-123",
            "context_id": "ctx-1",
            "created_at": "2025-01-01T00:00:00Z",
        }
        proc._setup_tracking_context = AsyncMock(
            return_value=(
                task_info,
                MagicMock(
                    room_id="room-1",
                    current_message=current_message,
                    agent_card=agent_card,
                    user_message_id="msg-1",
                    task_info=task_info,
                    send_sse=False,
                ),
            )
        )
        proc.a2a_transport.send_message_to_tracked_agent = AsyncMock(
            return_value={
                "type": "task",
                "requires_auth": True,
                "task_id": "task-abc",
                "message": "Please provide your OAuth token.",
            }
        )
        proc.tsm.notify_task = AsyncMock()

        await proc.handle_sync_response(
            current_message=current_message,
            agent_card=agent_card,
            prepared_message=MagicMock(),
            room_id="room-1",
            _user_id="user-1",
            user_message_id="msg-1",
        )

        task = current_message.message_content.message_task
        assert task is not None
        assert task.status.state == CommonTaskState.AUTH_REQUIRED
        assert task.status.message is None
        assert not hasattr(proc, "_internal_interactive_status_messages")


class TestDispatchInteractive:
    """dispatch() maps interactive task states to AWAITING_INPUT for HITL."""

    @pytest.mark.asyncio
    async def test_dispatch_auth_required_returns_awaiting_input_with_prompt(self):
        proc = _make_processor()
        message = _make_room_agent_message()
        task = message.message_content.message_task
        assert task is not None
        task.status.state = TaskState.auth_required
        task.status.message = Message(
            message_id="status-msg-1",
            role=MessageRole.AGENT,
            parts=[TextPart(kind="text", text="Please provide your OAuth token.")],
        )

        proc.a2a_transport.has_streaming_capability = MagicMock(return_value=False)
        proc.handle_sync_response = AsyncMock(
            return_value=(True, None, message.message_id, "agent-task-auth")
        )

        agent = MagicMock()
        agent.agent_card = MagicMock()
        ctx = DispatchContext(
            agent=agent,
            room_agent_message=message,
            room_id="room-1",
            user_message_id="user-msg-1",
            prepared_message=MagicMock(),
        )

        result = await proc.dispatch(ctx, message)

        assert result.status == ProcessingStatus.AWAITING_INPUT
        assert result.message_id == message.message_id
        assert result.a2a_task_id == "agent-task-auth"
        assert result.status_message == "Authentication required"

    @pytest.mark.asyncio
    async def test_dispatch_auth_required_without_message_uses_default_prompt(self):
        proc = _make_processor()
        message = _make_room_agent_message()
        task = message.message_content.message_task
        assert task is not None
        task.status.state = CommonTaskState.AUTH_REQUIRED
        task.status.message = None

        proc.a2a_transport.has_streaming_capability = MagicMock(return_value=False)
        proc.handle_sync_response = AsyncMock(
            return_value=(True, None, message.message_id, "agent-task-auth")
        )

        agent = MagicMock()
        agent.agent_card = MagicMock()
        ctx = DispatchContext(
            agent=agent,
            room_agent_message=message,
            room_id="room-1",
            user_message_id="user-msg-1",
            prepared_message=MagicMock(),
        )

        result = await proc.dispatch(ctx, message)

        assert result.status == ProcessingStatus.AWAITING_INPUT
        assert result.status_message == "Authentication required"

    @pytest.mark.asyncio
    async def test_dispatch_requires_auth_without_status_returns_awaiting_input(self):
        private_prompt = "PRIVATE_SENTINEL_direct_auth_prompt"
        proc = _make_processor()
        message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"

        task_info = {
            "webhook_token": "tok-123",
            "context_id": "ctx-1",
            "created_at": "2025-01-01T00:00:00Z",
        }
        proc._setup_tracking_context = AsyncMock(
            return_value=(
                task_info,
                MagicMock(
                    room_id="room-1",
                    current_message=message,
                    agent_card=agent_card,
                    user_message_id="user-msg-1",
                    task_info=task_info,
                    send_sse=False,
                ),
            )
        )
        proc.a2a_transport.has_streaming_capability = MagicMock(return_value=False)
        proc.a2a_transport.send_message_to_tracked_agent = AsyncMock(
            return_value={
                "type": "task",
                "requires_auth": True,
                "task_id": "agent-task-auth",
                "message": private_prompt,
            }
        )
        proc.tsm.notify_task = AsyncMock()

        agent = MagicMock()
        agent.agent_card = agent_card
        ctx = DispatchContext(
            agent=agent,
            room_agent_message=message,
            room_id="room-1",
            user_message_id="user-msg-1",
            prepared_message=MagicMock(),
        )

        result = await proc.dispatch(ctx, message)

        assert result.status == ProcessingStatus.AWAITING_INPUT
        assert result.status_message == "Authentication required"
        assert private_prompt not in json.dumps(
            result.__dict__, sort_keys=True, default=str
        )
        assert not hasattr(proc, "_internal_interactive_status_messages")
        task = message.message_content.message_task
        assert task is not None
        assert task.status.state == CommonTaskState.AUTH_REQUIRED

    @pytest.mark.asyncio
    async def test_dispatch_non_push_polled_interactive_keeps_raw_prompt_internal_only(
        self,
    ):
        private_prompt = "PRIVATE_SENTINEL_polled_interactive_prompt"
        proc = _make_processor()
        message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "test-agent"
        task_info = {
            "webhook_token": "tok-123",
            "context_id": "ctx-1",
            "created_at": "2025-01-01T00:00:00Z",
        }
        processing_ctx = ProcessingContext(
            room_id="room-1",
            current_message=message,
            agent_card=agent_card,
            user_message_id="user-msg-1",
            task_info=task_info,
            send_sse=False,
        )
        proc._setup_tracking_context = AsyncMock(
            return_value=(task_info, processing_ctx)
        )
        proc.a2a_transport.has_streaming_capability = MagicMock(return_value=False)
        proc.a2a_transport.has_push_notification_capability = MagicMock(
            return_value=False
        )
        proc.a2a_transport.send_message_to_tracked_agent = AsyncMock(
            return_value={
                "type": "task",
                "task_id": "remote-task-42",
                "status": TaskState.working,
            }
        )
        proc._poll_task_until_complete = AsyncMock(
            return_value=Task(
                id="remote-task-42",
                contextId="ctx-1",
                status=TaskStatus(
                    state=TaskState.input_required,
                    message=Message(
                        message_id="private-status-msg",
                        role=MessageRole.AGENT,
                        parts=[TextPart(kind="text", text=private_prompt)],
                    ),
                ),
                artifacts=[
                    {
                        "artifactId": "private-artifact",
                        "parts": [{"kind": "text", "text": private_prompt}],
                    }
                ],
                kind="task",
            )
        )
        proc._task_updater.update_task_on_message = AsyncMock(return_value=True)
        proc.tsm.notify_task = AsyncMock()
        proc.response_handler.handle = AsyncMock()
        agent = MagicMock()
        agent.agent_card = agent_card
        ctx = DispatchContext(
            agent=agent,
            room_agent_message=message,
            room_id="room-1",
            user_message_id="user-msg-1",
            prepared_message=MagicMock(),
        )

        result = await proc.dispatch(ctx, message)

        assert result.status == ProcessingStatus.AWAITING_INPUT
        assert result.status_message == "Requesting test-agent"
        assert private_prompt not in json.dumps(
            result.__dict__, sort_keys=True, default=str
        )
        persisted_task = proc._task_updater.update_task_on_message.await_args.args[1]
        persisted_json = json.dumps(persisted_task, sort_keys=True)
        in_memory_json = message.message_content.message_task.model_dump_json()
        assert private_prompt not in persisted_json
        assert private_prompt not in in_memory_json
        assert proc.response_handler.handle.await_args is None


class TestFinalizePolledTaskPrivacy:
    def _make_completed_task_with_private_history(self, private_text: str) -> Task:
        return Task(
            id="remote-task-1",
            contextId="ctx-1",
            status=TaskStatus(state=TaskState.completed),
            history=[
                Message(
                    role=MessageRole.USER,
                    message_id="private-user-history",
                    parts=[TextPart(kind="text", text=private_text)],
                ),
                Message(
                    role=MessageRole.AGENT,
                    message_id="agent-history",
                    parts=[TextPart(kind="text", text="Visible agent answer")],
                ),
            ],
            kind="task",
        )

    def _make_interactive_task_with_private_history(self, private_text: str) -> Task:
        return Task(
            id="remote-task-1",
            contextId="ctx-1",
            status=TaskStatus(
                state=TaskState.input_required,
                message=Message(
                    role=MessageRole.AGENT,
                    message_id="status-message",
                    parts=[TextPart(kind="text", text="Need approval")],
                ),
            ),
            history=[
                Message(
                    role=MessageRole.USER,
                    message_id="private-user-history",
                    parts=[TextPart(kind="text", text=private_text)],
                ),
                Message(
                    role=MessageRole.AGENT,
                    message_id="agent-history",
                    parts=[TextPart(kind="text", text="Visible prompt")],
                ),
            ],
            kind="task",
        )

    def _make_failed_task_with_private_terminal_data(self, private_text: str) -> Task:
        return Task(
            id="remote-task-1",
            contextId="ctx-1",
            status=TaskStatus(
                state=TaskState.failed,
                message=Message(
                    role=MessageRole.AGENT,
                    message_id="private-status",
                    parts=[TextPart(kind="text", text=private_text)],
                ),
            ),
            history=[
                Message(
                    role=MessageRole.AGENT,
                    message_id="private-history",
                    parts=[TextPart(kind="text", text=private_text)],
                )
            ],
            artifacts=[
                Artifact(
                    artifactId="partial-artifact",
                    name="partial",
                    parts=[TextPart(kind="text", text=private_text)],
                )
            ],
            metadata={"remote_error": private_text},
            kind="task",
        )

    def _make_ctx(self, current_message, agent_card):
        return ProcessingContext(
            room_id="room-1",
            current_message=current_message,
            agent_card=agent_card,
            user_message_id="user-msg-1",
            task_info={"webhook_token": "tok"},
            send_sse=False,
        )

    @pytest.mark.asyncio
    async def test_terminal_polled_task_does_not_use_history_as_public_output(self):
        private_text = "PRIVATE_SENTINEL_polled_terminal_history"
        proc = _make_processor()
        proc._task_updater.update_task_on_message = AsyncMock(return_value=True)
        proc.response_handler.handle = AsyncMock()
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "Agent One"

        await proc._finalize_polled_task(
            self._make_completed_task_with_private_history(private_text),
            current_message,
            agent_card,
            room_id="room-1",
            message_id="agent-msg-1",
            task_info={"webhook_token": "tok"},
            ctx=self._make_ctx(current_message, agent_card),
        )

        persisted_task = proc._task_updater.update_task_on_message.await_args.args[1]
        persisted_json = json.dumps(persisted_task, sort_keys=True)
        assert private_text not in persisted_json
        assert persisted_task["history"] is None
        assert "Visible agent answer" not in persisted_json
        emitted_event = proc.response_handler.handle.await_args.args[0]
        assert emitted_event.text == "Requesting Agent One"
        assert private_text not in json.dumps(emitted_event.__dict__)

    @pytest.mark.asyncio
    async def test_interactive_polled_task_keeps_in_memory_and_persisted_history_public(
        self,
    ):
        private_text = "PRIVATE_SENTINEL_polled_interactive_history"
        proc = _make_processor()
        proc._task_updater.update_task_on_message = AsyncMock(return_value=True)
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "Agent One"

        await proc._finalize_polled_task(
            self._make_interactive_task_with_private_history(private_text),
            current_message,
            agent_card,
            room_id="room-1",
            message_id="agent-msg-1",
            task_info={"webhook_token": "tok"},
            ctx=self._make_ctx(current_message, agent_card),
        )

        persisted_task = proc._task_updater.update_task_on_message.await_args.args[1]
        persisted_json = json.dumps(persisted_task, sort_keys=True)
        in_memory_json = current_message.message_content.message_task.model_dump_json()
        assert private_text not in persisted_json
        assert private_text not in in_memory_json
        assert "Visible prompt" not in persisted_json
        assert "Visible prompt" not in in_memory_json

    @pytest.mark.asyncio
    async def test_failed_polled_task_degraded_output_and_memory_use_public_projection(
        self,
    ):
        private_text = "PRIVATE_SENTINEL_polled_failed_terminal"
        proc = _make_processor()
        proc.delivery.send_task_update = AsyncMock()
        current_message = _make_room_agent_message()
        agent_card = MagicMock(spec_set=["name"])
        agent_card.name = "Agent One"

        result = await proc._finalize_polled_task(
            self._make_failed_task_with_private_terminal_data(private_text),
            current_message,
            agent_card,
            room_id="room-1",
            message_id="agent-msg-1",
            task_info=None,
            ctx=self._make_ctx(current_message, agent_card),
        )

        assert result == (False, "Task failed", None, None)
        delivery_payload = proc.delivery.send_task_update.await_args.kwargs
        in_memory_json = current_message.message_content.message_task.model_dump_json()
        assert delivery_payload["content"] is None
        assert delivery_payload["error"] == "Task failed"
        assert private_text not in json.dumps(delivery_payload)
        assert private_text not in in_memory_json

    @pytest.mark.asyncio
    async def test_room_task_persistence_drops_history_and_keeps_artifacts(self):
        private_text = "PRIVATE_SENTINEL_room_task_persistence_history"
        proc = _make_processor()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        message = _make_room_agent_message()
        returned_task = self._make_completed_task_with_private_history(private_text)
        returned_task.artifacts = [
            Artifact(
                artifact_id="artifact-answer",
                name="response",
                parts=[TextPart(kind="text", text="Visible artifact answer")],
            )
        ]

        result = await proc._handle_a2a_response_for_room(message, returned_task)

        assert result is True
        persisted_message = proc.tsm.persist_message.await_args.args[0]
        persisted_json = (
            persisted_message.message_content.message_task.model_dump_json()
        )
        assert private_text not in persisted_json
        assert "Visible agent answer" not in persisted_json
        assert "Visible artifact answer" in persisted_json
        assert persisted_message.message_content.message_task.history is None

    @pytest.mark.asyncio
    async def test_room_message_response_for_noncompleted_task_is_discarded(self):
        private_text = "PRIVATE_SENTINEL_noncompleted_message_response"
        proc = _make_processor()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        message = _make_room_agent_message()
        returned_message = Message(
            role=MessageRole.AGENT,
            message_id="agent-message",
            parts=[Part(root=TextPart(text=private_text))],
        )

        result = await proc._handle_a2a_response_for_room(message, returned_message)

        assert result is True
        persisted_message = proc.tsm.persist_message.await_args.args[0]
        persisted_task = persisted_message.message_content.message_task
        assert persisted_task.status.state == TaskState.working
        assert persisted_task.history is None
        assert persisted_task.artifacts is None
        assert private_text not in persisted_task.model_dump_json()

    @pytest.mark.asyncio
    async def test_completed_room_message_response_materializes_sanitized_artifact(
        self,
    ):
        message_metadata_sentinel = "PRIVATE_SENTINEL_agent_message_metadata"
        part_metadata_sentinel = "PRIVATE_SENTINEL_agent_part_metadata"
        public_text = "Visible agent answer"
        proc = _make_processor()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        message = _make_room_agent_message(
            message_content=MessageContent(
                message_text="",
                message_task=Task(
                    id="task-001",
                    contextId="ctx-001",
                    status=TaskStatus(state=TaskState.completed),
                    kind="task",
                ),
            )
        )
        returned_message = Message(
            role=MessageRole.AGENT,
            message_id="agent-message-with-private-metadata",
            parts=[
                Part(
                    root=TextPart(
                        text=public_text,
                        metadata={"private": part_metadata_sentinel},
                    )
                )
            ],
            metadata={"private": message_metadata_sentinel},
        )

        result = await proc._handle_a2a_response_for_room(message, returned_message)

        assert result is True
        persisted_message = proc.tsm.persist_message.await_args.args[0]
        persisted_task = persisted_message.message_content.message_task
        persisted_json = persisted_task.model_dump_json()
        assert message_metadata_sentinel not in persisted_json
        assert part_metadata_sentinel not in persisted_json
        assert public_text in persisted_json
        assert persisted_task.history is None
        assert persisted_task.artifacts is not None
        assert persisted_task.artifacts[0].name == "response"
        assert persisted_task.artifacts[0].parts[0].root.text == public_text
