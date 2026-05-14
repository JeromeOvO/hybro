"""
Unit tests for DirectTransport module.

Tests cover:
- _parse_sync_fallback_response: None input, message kind, task kind,
  JSONRPCErrorResponse, and default fallback
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

from a2a.types import (
    JSONRPCError,
    JSONRPCErrorResponse,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)

from common.utils.cancellation import CancellationToken
from models.processing import ProcessingContext, ProcessingStatus
from models.room import MessageContent, RoomAgentMessage
from modules.transports.direct import DirectTransport, MessageStreamingState
from services.a2a_service import A2AServiceError


# =============================================================================
# _parse_sync_fallback_response Tests
# =============================================================================


class TestParseSyncFallbackResponse:
    """Tests for sync response parsing into normalized dict."""

    def test_returns_empty_for_none(self):
        result = DirectTransport._parse_sync_fallback_response(None, "msg-1")
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

        result = DirectTransport._parse_sync_fallback_response(response, "msg-1")
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

        result = DirectTransport._parse_sync_fallback_response(response, "msg-1")
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
            DirectTransport._parse_sync_fallback_response(response, "msg-1")

    def test_unknown_kind_returns_empty(self):
        inner_result = MagicMock()
        inner_result.kind = "unknown"

        root = MagicMock()
        root.result = inner_result

        response = MagicMock()
        response.root = root

        assert not isinstance(response.root, JSONRPCErrorResponse)

        result = DirectTransport._parse_sync_fallback_response(response, "msg-1")
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

        result = DirectTransport._parse_sync_fallback_response(response, "msg-1")
        assert result["content"] == "Hello world"


# =============================================================================
# Instance-method tests — bypass __init__ via object.__new__, inject mocks
# =============================================================================


def _make_processor(**overrides):
    """Create a DirectTransport with mocked dependencies, bypassing __init__."""
    proc = object.__new__(DirectTransport)
    proc.response_handler = overrides.get("response_handler", MagicMock())
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
        proc._finalize_polled_task = AsyncMock(return_value=(True, "done", None,None))
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

        @asynccontextmanager
        async def _fake_create_a2a_client(*args, **kwargs):
            yield a2a_client

        proc.a2a_service.create_a2a_client = _fake_create_a2a_client

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
        proc.response_handler.handle = AsyncMock()

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
        proc.response_handler.handle.assert_awaited_once()


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
        assert current_message.message_content.message_text == "Final answer from agent."


# =============================================================================
# Phase 1: artifact_update streaming tests
# =============================================================================


class TestMessageChunkEmitsArtifactUpdate:
    """_handle_stream_message_chunk emits send_artifact_update."""

    @pytest.mark.asyncio
    async def test_message_chunk_emits_artifact_update(self):
        proc = _make_processor()
        proc.sse_manager.send_artifact_update = AsyncMock()
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

        text_part = TextPart(kind="text", text="Hello")

        result = MagicMock()
        result.parts = [text_part]
        result.role = "agent"
        result.message_id = "a2a-msg-1"

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            "common.utils.a2a_helpers.extract_parts",
            lambda parts: MagicMock(text="Hello", has_non_text=False, file_parts=[], data_parts=[]),
        )

        await proc._handle_stream_message_chunk(result, ctx, streaming_state)

        monkeypatch.undo()

        proc.sse_manager.send_artifact_update.assert_awaited_once()
        call_args = proc.sse_manager.send_artifact_update.call_args
        assert call_args[0][0] == "room-1"  # room_id
        artifact = call_args[0][3]  # artifact_dict
        assert artifact["artifact_id"] == "msg-1-stream"
        assert artifact["parts"] == [{"kind": "text", "text": "Hello"}]
        assert call_args[1]["append"] is True or call_args[0][4] is True

    @pytest.mark.asyncio
    async def test_message_chunk_skips_empty_content(self):
        """Empty text content should not emit any SSE event."""
        proc = _make_processor()
        proc.sse_manager.send_artifact_update = AsyncMock()
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
        result.role = "agent"
        result.message_id = "a2a-msg-1"

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            "common.utils.a2a_helpers.extract_parts",
            lambda parts: MagicMock(text="", has_non_text=False, file_parts=[], data_parts=[]),
        )

        await proc._handle_stream_message_chunk(result, ctx, streaming_state)

        monkeypatch.undo()

        proc.sse_manager.send_artifact_update.assert_not_awaited()


class TestArtifactUpdateRoutedThroughHandler:
    """_handle_stream_artifact_update routes through response_handler.handle
    instead of using tsm.persist_message + sse_manager.send_artifact_update."""

    @pytest.mark.asyncio
    async def test_artifact_chunk_routes_through_handler(self):
        proc = _make_processor()
        proc.response_handler.handle = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc.sse_manager.send_artifact_update = AsyncMock()

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
        artifact.model_dump = MagicMock(return_value={"artifact_id": "art-1", "parts": []})

        result = MagicMock()
        result.artifact = artifact
        result.append = True
        result.last_chunk = False

        proc._convert_inline_bytes_to_s3 = AsyncMock()

        await proc._handle_stream_artifact_update(result, ctx, streaming_state)

        # Handler receives the event, NOT direct sse_manager or persist_message
        proc.response_handler.handle.assert_awaited_once()
        event = proc.response_handler.handle.call_args[0][0]
        assert event.kind == "artifact_update"
        assert event.s3_converted is True
        assert event.append is True
        assert event.last_chunk is False
        assert event.artifacts == [{"artifact_id": "art-1", "parts": []}]

        # Old paths should NOT be called
        proc.tsm.persist_message.assert_not_awaited()
        proc.sse_manager.send_artifact_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_artifact_chunk_no_sse_uses_atomic_persist(self):
        """When send_sse=False, persists via accumulate_artifact_on_message (atomic)."""
        proc = _make_processor()
        proc.response_handler.handle = AsyncMock()
        proc.tsm.persist_message = AsyncMock(return_value=True)
        proc.database_service.accumulate_artifact_on_message = AsyncMock(return_value=True)

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
        artifact.model_dump = MagicMock(return_value={"artifact_id": "art-1", "parts": []})

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
        # Instead uses atomic accumulate
        proc.database_service.accumulate_artifact_on_message.assert_awaited_once_with(
            "msg-1", {"artifact_id": "art-1", "parts": []}, append=False,
        )


class TestFinalizeStreamingEmitsLastChunk:
    """_finalize_streaming sends a last_chunk=True artifact_update event."""

    @pytest.mark.asyncio
    async def test_finalize_emits_last_chunk(self):
        proc = _make_processor()
        proc.sse_manager.send_artifact_update = AsyncMock()
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
        # First call should be the last_chunk artifact_update
        first_call = proc.sse_manager.send_artifact_update.call_args_list[0]
        assert first_call[0][0] == "room-1"
        artifact = first_call[0][3]
        assert artifact["artifact_id"] == "msg-1-stream"
        assert artifact["parts"] == []
        assert first_call[1]["last_chunk"] is True or first_call[0][5] is True


# =============================================================================
# _process_sync_response: persist gating on tracked vs degraded path
# =============================================================================


class TestProcessSyncResponsePersistGating:
    """Tracked path skips full-document persist to avoid overwriting
    the real task saved by a2a_service's partial $set."""

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

    @pytest.mark.asyncio
    async def test_degraded_path_persists(self):
        """When task_info is None (degraded/fallback path), persist=True."""
        proc = _make_processor()
        proc.tsm.transition_task = AsyncMock()
        proc.sse_manager.send_task_update = AsyncMock()
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
            "error": "Apify actor timed out",
            "persisted": True,
        }

        success, text, _ , agent_task_id= await proc._process_sync_response(
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
        assert handle_call.error_text == "Apify actor timed out"

        # Return text should be the error message
        assert text == "Apify actor timed out"

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

        success, text, _ ,agent_task_id= await proc._process_sync_response(
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
        proc.a2a_service.send_message_to_tracked_agent = AsyncMock(
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
