"""
Unit tests for ResponseProcessor module.

Tests cover:
- _parse_sync_fallback_response: None input, message kind, task kind,
  JSONRPCErrorResponse, and default fallback
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from a2a.types import (
    JSONRPCError,
    JSONRPCErrorResponse,
    Task,
    TaskState,
    TaskStatus,
)

from common.utils.cancellation import CancellationToken
from models.processing import ProcessingContext, ProcessingStatus
from models.room import MessageContent, RoomAgentMessage
from modules.ResponseProcessor import MessageStreamingState, ResponseProcessor
from services.a2a_service import A2AServiceError


# =============================================================================
# _parse_sync_fallback_response Tests
# =============================================================================


class TestParseSyncFallbackResponse:
    """Tests for sync response parsing into normalized dict."""

    def test_returns_empty_for_none(self):
        result = ResponseProcessor._parse_sync_fallback_response(None, "msg-1")
        assert result == {"type": "message", "message_id": "msg-1", "content": ""}

    def test_parses_message_kind(self):
        part = MagicMock()
        part.text = "Hello"
        del part.root

        inner_result = MagicMock()
        inner_result.kind = "message"
        inner_result.parts = [part]

        root = MagicMock()
        root.result = inner_result

        response = MagicMock()
        response.root = root

        assert not isinstance(response.root, JSONRPCErrorResponse)

        result = ResponseProcessor._parse_sync_fallback_response(response, "msg-1")
        assert result["type"] == "message"
        assert result["content"] == "Hello"

    def test_parses_task_kind(self):
        inner_result = MagicMock()
        inner_result.kind = "task"
        inner_result.id = "task-001"
        inner_result.status = MagicMock()
        inner_result.status.state = TaskState.completed

        root = MagicMock()
        root.result = inner_result

        response = MagicMock()
        response.root = root

        assert not isinstance(response.root, JSONRPCErrorResponse)

        result = ResponseProcessor._parse_sync_fallback_response(response, "msg-1")
        assert result["type"] == "task"
        assert result["task_id"] == "task-001"
        assert result["status"] == "completed"

    def test_raises_on_jsonrpc_error(self):
        error_response = JSONRPCErrorResponse(
            id="req-1",
            error=JSONRPCError(code=-32000, message="Agent offline"),
        )

        response = MagicMock()
        response.root = error_response

        with pytest.raises(A2AServiceError):
            ResponseProcessor._parse_sync_fallback_response(response, "msg-1")

    def test_unknown_kind_returns_empty(self):
        inner_result = MagicMock()
        inner_result.kind = "unknown"

        root = MagicMock()
        root.result = inner_result

        response = MagicMock()
        response.root = root

        assert not isinstance(response.root, JSONRPCErrorResponse)

        result = ResponseProcessor._parse_sync_fallback_response(response, "msg-1")
        assert result == {"type": "message", "message_id": "msg-1", "content": ""}

    def test_concatenates_multiple_text_parts(self):
        p1 = MagicMock()
        p1.text = "Hello "
        del p1.root
        p2 = MagicMock()
        p2.text = "world"
        del p2.root

        inner_result = MagicMock()
        inner_result.kind = "message"
        inner_result.parts = [p1, p2]

        root = MagicMock()
        root.result = inner_result

        response = MagicMock()
        response.root = root

        assert not isinstance(response.root, JSONRPCErrorResponse)

        result = ResponseProcessor._parse_sync_fallback_response(response, "msg-1")
        assert result["content"] == "Hello world"


# =============================================================================
# Instance-method tests — bypass __init__ via object.__new__, inject mocks
# =============================================================================


def _make_processor(**overrides):
    """Create a ResponseProcessor with mocked dependencies, bypassing __init__."""
    proc = object.__new__(ResponseProcessor)
    proc.tsm = overrides.get("tsm", MagicMock())
    proc.sse_manager = overrides.get("sse_manager", MagicMock())
    proc.a2a_service = overrides.get("a2a_service", MagicMock())
    proc.task_service = overrides.get("task_service", MagicMock())
    proc.database_service = overrides.get("database_service", MagicMock())
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
        proc.a2a_service.send_message_to_tracked_agent = AsyncMock(
            return_value={"type": "message", "content": "Hello from agent"}
        )
        proc.tsm.transition_task = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)

        with patch(
            "modules.ResponseProcessor.notify_task_update", new_callable=AsyncMock
        ) as mock_notify:
            success, text, paused = await proc.handle_sync_response(
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
        proc.a2a_service.send_message_to_tracked_agent = AsyncMock(
            return_value={
                "type": "task",
                "task_id": "remote-task-42",
                "status": TaskState.working,
            }
        )
        proc.a2a_service.has_push_notification_capability = MagicMock(return_value=False)
        proc.tsm.transition_task = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc.tsm.notify_task = AsyncMock()

        completed_task = MagicMock()
        completed_task.status.state = TaskState.completed
        completed_task.artifacts = None
        completed_task.model_dump = MagicMock(return_value={})

        proc._poll_task_until_complete = AsyncMock(return_value=completed_task)
        proc._finalize_polled_task = AsyncMock(return_value=(True, "done", None))

        with patch(
            "modules.ResponseProcessor.notify_task_update", new_callable=AsyncMock
        ):
            success, text, paused = await proc.handle_sync_response(
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
    async def test_poll_task_until_complete_reaches_terminal(self):
        proc = _make_processor()
        agent_card = MagicMock()

        working_task = MagicMock()
        working_task.status.state = TaskState.working

        completed_task = MagicMock()
        completed_task.status.state = TaskState.completed

        # Build responses whose .root is not a JSONRPCErrorResponse instance.
        # Using a simple namespace avoids MagicMock spec issues.
        class _FakeRoot:
            def __init__(self, result):
                self.result = result

        class _FakeResponse:
            def __init__(self, result):
                self.root = _FakeRoot(result)

        working_resp = _FakeResponse(working_task)
        completed_resp = _FakeResponse(completed_task)

        a2a_client = AsyncMock()
        a2a_client.get_task = AsyncMock(
            side_effect=[working_resp, working_resp, completed_resp]
        )
        proc.a2a_service.create_a2a_client = AsyncMock(return_value=a2a_client)

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
        assert a2a_client.get_task.await_count == 3


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

        with patch(
            "modules.ResponseProcessor.notify_task_update", new_callable=AsyncMock
        ) as mock_notify:
            status, text = await proc._handle_streaming_cancellation(
                ctx, streaming_state
            )

        assert status == ProcessingStatus.CANCELED
        assert text == "partial text"
        proc.tsm.transition_task.assert_awaited_once()
        assert proc.tsm.transition_task.call_args[0][1] == TaskState.canceled
        proc._try_cancel_remote_task.assert_awaited_once_with(
            current_message, agent_card
        )
        mock_notify.assert_awaited_once()


class TestFinalizeStreamingWritesArtifacts:
    """_finalize_streaming transitions to completed and calls notify_task_update
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

        with patch(
            "modules.ResponseProcessor.notify_task_update", new_callable=AsyncMock
        ) as mock_notify:
            status, text = await proc._finalize_streaming(ctx, streaming_state)

        assert status == ProcessingStatus.SUCCESS
        assert text == "Final answer from agent."
        proc.tsm.transition_task.assert_awaited_once()
        assert proc.tsm.transition_task.call_args[0][1] == TaskState.completed
        mock_notify.assert_awaited_once()
        notify_kwargs = mock_notify.call_args.kwargs
        assert notify_kwargs["message_id"] == "msg-1"
        assert notify_kwargs["state"] == TaskState.completed
        assert notify_kwargs["room_id"] == "room-1"
        assert current_message.message_content.message_text == "Final answer from agent."
