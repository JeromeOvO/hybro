"""
Integration tests for AgentResponseHandler — parity verification.

Tests that feeding the same AgentEvent sequences through the handler
produces identical DB writes and SSE emissions for each event kind,
and that flow-control flags (skip_persist) work.
"""

import ast
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from modules.agent_event import AgentEvent
from execution.dispatch.response_handler import AgentResponseHandler


# =============================================================================
# Fixtures
# =============================================================================


def _make_handler(*, db=None, sse=None, rmc=None, hitl_coordinator=None):
    if db is None:
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=True)
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(return_value=None)
    if sse is None:
        sse = MagicMock()
        sse.send_agent_response = AsyncMock()
        sse.send_artifact_update = AsyncMock()
        sse.send_task_submitted = AsyncMock()
        sse.send_task_update = AsyncMock()
        sse.send_processing_status = AsyncMock()
        sse.send_error = AsyncMock()
    if rmc is None:
        rmc = MagicMock()
        rmc.resume_queue_from_continuation = AsyncMock(return_value=True)
    return AgentResponseHandler(
        db=db,
        sse=sse,
        room_message_center=rmc,
        hitl_coordinator=hitl_coordinator,
    )


def _base_event(**overrides):
    defaults = dict(
        message_id="msg-001",
        room_id="room-001",
        agent_id="agent-001",
        user_id="user-001",
        related_message_id="umsg-001",
    )
    defaults.update(overrides)
    return defaults


def test_processing_status_callback_has_no_required_post_emit_business_side_effects():
    path = Path(__file__).resolve().parents[1] / "execution" / "dispatch" / "response_handler.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_on_processing_status"
    )
    emit_lines = [
        node.lineno
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_emit_processing_status"
    ]
    assert emit_lines
    last_emit = max(emit_lines)
    forbidden_after_emit = {
        "record_and_maybe_broadcast_run_event",
        "update_task_state_on_message",
        "accumulate_artifact_on_message",
        "resume_queue_from_continuation",
        "request_input",
    }
    post_emit = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or node.lineno <= last_emit:
            continue
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        else:
            name = None
        if name in forbidden_after_emit:
            post_emit.append((node.lineno, ast.unparse(node)))
    assert post_emit == []


# =============================================================================
# Artifact update events
# =============================================================================


class TestArtifactUpdateEvent:
    @pytest.mark.asyncio
    async def test_persists_and_sends_sse(self):
        h = _make_handler()
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            text="chunk", artifacts=[{"id": "a1"}],
        )
        await h.handle(event)
        h._db.accumulate_artifact_on_message.assert_awaited_once_with(
            "msg-001", {"id": "a1"}, append=False,
        )
        h._sse.send_artifact_update.assert_awaited_once_with(
            room_id="room-001",
            message_id="msg-001",
            agent_id="agent-001",
            artifact={"id": "a1"},
            append=False,
            last_chunk=False,
        )

    @pytest.mark.asyncio
    async def test_skip_persist_still_broadcasts(self):
        h = _make_handler()
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            text="chunk", artifacts=[{"id": "a1"}], skip_persist=True,
        )
        await h.handle(event)
        h._db.accumulate_artifact_on_message.assert_not_awaited()
        h._sse.send_artifact_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_append_flag_passed(self):
        h = _make_handler()
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            text="chunk", artifacts=[{"id": "a1"}], append=True,
        )
        await h.handle(event)
        h._db.accumulate_artifact_on_message.assert_awaited_once_with(
            "msg-001", {"id": "a1"}, append=True,
        )
        h._sse.send_artifact_update.assert_awaited_once()
        call_kwargs = h._sse.send_artifact_update.call_args.kwargs
        assert call_kwargs["append"] is True

    @pytest.mark.asyncio
    async def test_last_chunk_flag_passed(self):
        h = _make_handler()
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            artifacts=[{"id": "a1"}], append=True, last_chunk=True,
        )
        await h.handle(event)
        call_kwargs = h._sse.send_artifact_update.call_args.kwargs
        assert call_kwargs["last_chunk"] is True

    @pytest.mark.asyncio
    async def test_no_artifacts_sends_artifact_update_for_text(self):
        """Text-only artifact_update (no artifact object) wraps text as artifact."""
        h = _make_handler()
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            text="chunk", artifacts=None, append=True, last_chunk=False,
        )
        await h.handle(event)
        h._db.accumulate_artifact_on_message.assert_not_awaited()
        h._sse.send_artifact_update.assert_awaited_once()
        call_kwargs = h._sse.send_artifact_update.call_args.kwargs
        assert call_kwargs["artifact"]["artifact_id"] == "msg-001-stream"
        assert call_kwargs["artifact"]["parts"] == [{"kind": "text", "text": "chunk"}]

    @pytest.mark.asyncio
    async def test_no_artifacts_no_text_sends_nothing(self):
        h = _make_handler()
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            text="", artifacts=None,
        )
        await h.handle(event)
        h._db.accumulate_artifact_on_message.assert_not_awaited()
        h._sse.send_artifact_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_artifact_with_file_parts_broadcasts(self):
        """Artifact with file parts is broadcast via send_artifact_update."""
        h = _make_handler()
        artifact = {
            "artifactId": "a1",
            "parts": [{"kind": "file", "file": {"bytes": "dGVzdA==", "mime_type": "text/plain"}}],
        }
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            artifacts=[artifact],
        )

        with pytest.MonkeyPatch.context() as mp:
            # Patch S3 conversion to avoid actual S3 calls
            mp.setattr(
                "common.utils.a2a_helpers.convert_inline_bytes_to_s3",
                AsyncMock(return_value=1),
            )
            await h.handle(event)

        h._sse.send_artifact_update.assert_awaited_once()
        h._db.accumulate_artifact_on_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_s3_converted_flag_skips_conversion(self):
        """When s3_converted=True, handler skips S3 conversion."""
        h = _make_handler()
        artifact = {
            "artifactId": "a1",
            "parts": [{"kind": "file", "file": {"bytes": "dGVzdA==", "mime_type": "text/plain"}}],
        }
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            artifacts=[artifact],
            s3_converted=True,
        )

        with pytest.MonkeyPatch.context() as mp:
            mock_convert = AsyncMock(return_value=1)
            mp.setattr(
                "common.utils.a2a_helpers.convert_inline_bytes_to_s3",
                mock_convert,
            )
            await h.handle(event)

        # S3 conversion should NOT be called (already done by transport)
        mock_convert.assert_not_awaited()
        # But SSE and DB should still fire
        h._sse.send_artifact_update.assert_awaited_once()
        h._db.accumulate_artifact_on_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_s3_conversion_failure_does_not_block_sse(self):
        """S3 conversion failure should not prevent SSE broadcast or DB persist."""
        h = _make_handler()
        artifact = {
            "artifactId": "a1",
            "parts": [{"kind": "file", "file": {"bytes": "dGVzdA==", "mime_type": "text/plain"}}],
        }
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            artifacts=[artifact],
        )

        with pytest.MonkeyPatch.context() as mp:
            mock_convert = AsyncMock(side_effect=RuntimeError("S3 unavailable"))
            mp.setattr(
                "common.utils.a2a_helpers.convert_inline_bytes_to_s3",
                mock_convert,
            )
            await h.handle(event)

        # SSE should still be sent despite S3 failure
        h._sse.send_artifact_update.assert_awaited_once()
        # DB persist should still happen
        h._db.accumulate_artifact_on_message.assert_awaited_once()


# =============================================================================
# Response events (terminal)
# =============================================================================


class TestResponseEvent:
    @pytest.mark.asyncio
    async def test_persists_completed_and_resumes(self):
        h = _make_handler()
        event = AgentEvent(kind="response", **_base_event(), text="Done!")

        with pytest.MonkeyPatch.context() as mp:
            mock_notify = AsyncMock(return_value=True)
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                mock_notify,
            )
            await h.handle(event)

        h._db.update_task_state_on_message.assert_awaited_once_with(
            "msg-001", "completed", message_text="Done!", artifacts=None,
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skip_persist_response(self):
        h = _make_handler()
        event = AgentEvent(
            kind="response", **_base_event(),
            text="Done!", skip_persist=True,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._db.update_task_state_on_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sends_agent_response_for_parts(self):
        h = _make_handler()
        event = AgentEvent(
            kind="response", **_base_event(),
            text="Done!", parts=[{"kind": "file"}],
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        # send_agent_response removed — _notify() delivers parts via task_update
        h._sse.send_agent_response.assert_not_awaited()


# =============================================================================
# Error events (terminal)
# =============================================================================


class TestErrorEvent:
    @pytest.mark.asyncio
    async def test_persists_error_state(self):
        h = _make_handler()
        event = AgentEvent(
            kind="error", **_base_event(),
            error_text="boom", state="failed",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._db.update_task_state_on_message.assert_awaited_once_with(
            "msg-001", "failed", message_text="boom",
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001", task_result_text=None, failed=True,
        )

    @pytest.mark.asyncio
    async def test_preserves_rejected_state(self):
        h = _make_handler()
        event = AgentEvent(
            kind="error", **_base_event(),
            error_text="nope", state="rejected",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._db.update_task_state_on_message.assert_awaited_once_with(
            "msg-001", "rejected", message_text="nope",
        )


# =============================================================================
# Canceled events (terminal)
# =============================================================================


class TestCanceledEvent:
    @pytest.mark.asyncio
    async def test_persists_canceled(self):
        h = _make_handler()
        event = AgentEvent(kind="canceled", **_base_event(), text="stopped")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._db.update_task_state_on_message.assert_awaited_once_with(
            "msg-001", "canceled", message_text="stopped",
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001", task_result_text=None, failed=True,
        )


# =============================================================================
# Interactive events
# =============================================================================


class TestInteractiveEvent:
    @pytest.mark.asyncio
    async def test_persists_interactive(self):
        h = _make_handler()
        event = AgentEvent(
            kind="interactive", **_base_event(),
            text="need input", state="input-required",
            task_id="t-1", context_id="c-1",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._db.update_task_state_on_message.assert_awaited_once_with(
            "msg-001", "input-required",
            message_text="need input", task_id="t-1", context_id="c-1",
        )
        h._rmc.resume_queue_from_continuation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_hitl_request_for_async_interactive_continuation(self):
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=True)
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(
            return_value={"user_message_id": "user-msg-001"}
        )
        db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=SimpleNamespace(message_id="display-msg-001")
        )
        db.get_room_by_room_id = AsyncMock(
            return_value=SimpleNamespace(room_agent_set={"agent-001": "Agent X"})
        )
        hitl = SimpleNamespace(
            request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-001"))
        )
        h = _make_handler(db=db, hitl_coordinator=hitl)
        event = AgentEvent(
            kind="interactive", **_base_event(),
            text="need input", state="input-required",
            task_id="t-1", context_id="c-1",
        )
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        hitl.request_input.assert_awaited_once_with(
            room_id="room-001",
            user_message_id="user-msg-001",
            source="agent",
            prompt="need input",
            agent_id="agent-001",
            agent_name="Agent X",
            a2a_task_id="t-1",
            a2a_context_id="c-1",
            continuation_message_id="msg-001",
            display_message_id="display-msg-001",
        )
        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="awaiting_input",
            message_id="user-msg-001",
            lifecycle_message_id="user-msg-001",
            record_lifecycle=True,
            client_request_id=None,
            details=None,
            legacy_details=None,
            error_message=None,
        )
        h._sse.send_processing_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_logs_agent_name_lookup_failure_without_blocking_hitl(self):
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=True)
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
        db.get_pending_continuation_on_message = AsyncMock(
            return_value={"user_message_id": "user-msg-001"}
        )
        db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=SimpleNamespace(message_id="display-msg-001")
        )
        db.get_room_by_room_id = AsyncMock(side_effect=RuntimeError("db down"))
        hitl = SimpleNamespace(
            request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-001"))
        )
        h = _make_handler(db=db, hitl_coordinator=hitl)
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        event = AgentEvent(
            kind="interactive", **_base_event(),
            text="need input", state="input-required",
            task_id="t-1", context_id="c-1",
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            debug = MagicMock()
            mp.setattr("execution.dispatch.response_handler.logger.debug", debug)
            await h.handle(event)

        debug.assert_called_once_with("agent name lookup failed", exc_info=True)
        hitl.request_input.assert_awaited_once()
        assert hitl.request_input.call_args.kwargs["agent_name"] is None
        emitter.assert_awaited_once()
        h._sse.send_processing_status.assert_not_awaited()


# =============================================================================
# Non-terminal events
# =============================================================================


class TestSubmittedEvent:
    @pytest.mark.asyncio
    async def test_sends_sse_submitted(self):
        h = _make_handler()
        event = AgentEvent(
            kind="task_submitted", **_base_event(),
            task_id="t-1", agent_name="Agent X",
        )
        await h.handle(event)
        h._sse.send_task_submitted.assert_awaited_once()
        h._db.update_task_state_on_message.assert_not_awaited()


class TestStatusUpdateEvent:
    @pytest.mark.asyncio
    async def test_sends_task_update_for_text(self):
        h = _make_handler()
        event = AgentEvent(
            kind="status_update", **_base_event(), text="still working",
        )
        await h.handle(event)
        h._sse.send_task_update.assert_awaited_once()
        call_kwargs = h._sse.send_task_update.call_args.kwargs
        assert call_kwargs["status"] == "working"
        assert call_kwargs["status_message"] == "still working"

    @pytest.mark.asyncio
    async def test_no_sse_for_empty_text(self):
        h = _make_handler()
        event = AgentEvent(
            kind="status_update", **_base_event(), text="",
        )
        await h.handle(event)
        h._sse.send_task_update.assert_not_awaited()


class TestProcessingStatusEvent:
    @pytest.mark.asyncio
    async def test_sends_processing_status(self):
        h = _make_handler()
        event = AgentEvent(
            kind="processing_status", **_base_event(),
            state="completed", details="all done",
        )
        await h.handle(event)
        h._sse.send_processing_status.assert_awaited_once_with(
            "room-001",
            "completed",
            "msg-001",
            details="all done",
            client_request_id=None,
            agents=None,
        )

    @pytest.mark.asyncio
    async def test_records_when_lifecycle_message_id_is_explicit(self):
        h = _make_handler()
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        event = AgentEvent(
            kind="processing_status", **_base_event(message_id="display-msg-001"),
            lifecycle_message_id="umsg-001",
            state="completed",
            details="all done",
            client_request_id="cr-1",
        )

        await h.handle(event)

        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="completed",
            message_id="display-msg-001",
            lifecycle_message_id="umsg-001",
            record_lifecycle=True,
            client_request_id="cr-1",
            details=None,
            legacy_details="all done",
            error_message="all done",
        )
        h._sse.send_processing_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_related_message_processing_status_stays_transport_only(self):
        h = _make_handler()
        emitter = AsyncMock()
        h.bind_execution_event_deps(emitter)
        event = AgentEvent(
            kind="processing_status", **_base_event(),
            state="completed", details="all done",
        )

        await h.handle(event)

        emitter.assert_awaited_once_with(
            room_id="room-001",
            status="completed",
            message_id="msg-001",
            lifecycle_message_id=None,
            record_lifecycle=False,
            client_request_id=None,
            details=None,
            legacy_details="all done",
            error_message="all done",
        )
        h._sse.send_processing_status.assert_not_awaited()


# =============================================================================
# Orchestration resume error handling
# =============================================================================


class TestResumeOrchestrationErrorHandling:
    @pytest.mark.asyncio
    async def test_resume_exception_does_not_propagate(self):
        rmc = MagicMock()
        rmc.resume_queue_from_continuation = AsyncMock(side_effect=RuntimeError("boom"))
        h = _make_handler(rmc=rmc)
        event = AgentEvent(
            kind="response", **_base_event(), text="Done!",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            # Should not raise despite resume failure
            await h.handle(event)


# =============================================================================
# Artifact text-only fallback (no artifact object, only text)
# =============================================================================


class TestArtifactTextFallback:
    """_on_artifact with text-only (no artifact) wraps text as artifact_update."""

    @pytest.mark.asyncio
    async def test_text_only_uses_artifact_update(self):
        h = _make_handler()
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            text="chunk", artifacts=None, append=True, last_chunk=False,
        )
        await h.handle(event)
        h._sse.send_artifact_update.assert_awaited_once()
        call_kwargs = h._sse.send_artifact_update.call_args.kwargs
        assert call_kwargs["artifact"]["artifact_id"] == "msg-001-stream"
        assert call_kwargs["artifact"]["parts"] == [{"kind": "text", "text": "chunk"}]
        assert call_kwargs["append"] is True
        assert call_kwargs["last_chunk"] is False


class TestStatusUpdateSendsTaskUpdate:
    """_on_status sends task_update for status text."""

    @pytest.mark.asyncio
    async def test_status_uses_task_update(self):
        h = _make_handler()
        event = AgentEvent(
            kind="status_update", **_base_event(), text="Searching the web...",
        )
        await h.handle(event)
        h._sse.send_task_update.assert_awaited_once()
        call_kwargs = h._sse.send_task_update.call_args.kwargs
        assert call_kwargs["status"] == "working"
        assert call_kwargs["status_message"] == "Searching the web..."
        assert call_kwargs["message_id"] == "msg-001"


# =============================================================================
# Handler-owned notify_task_update method
# =============================================================================


class TestHandlerNotifyTaskUpdate:
    """notify_task_update method delegates to _notify_task_update_impl."""

    @pytest.mark.asyncio
    async def test_delegates_to_shared_impl(self):
        h = _make_handler()

        with pytest.MonkeyPatch.context() as mp:
            mock_impl = AsyncMock(return_value=True)
            mp.setattr(
                "services.task_notification_service._notify_task_update_impl",
                mock_impl,
            )
            result = await h.notify_task_update(
                message_id="msg-001",
                state=MagicMock(value="completed"),
                room_id="room-001",
                user_id="user-001",
                error=None,
                parts=None,
            )

        assert result is True
        mock_impl.assert_awaited_once()
        call_args = mock_impl.call_args
        # First positional arg is the handler's db instance
        assert call_args[0][0] is h._db
        # Third positional arg is the handler's sse instance
        assert call_args[0][2] is h._sse

    @pytest.mark.asyncio
    async def test_notify_helper_delegates_to_method(self):
        """_notify helper calls self.notify_task_update with event fields."""
        h = _make_handler()

        with pytest.MonkeyPatch.context() as mp:
            mock_impl = AsyncMock(return_value=True)
            mp.setattr(
                "services.task_notification_service._notify_task_update_impl",
                mock_impl,
            )
            from a2a.types import TaskState

            event = AgentEvent(
                kind="response", **_base_event(),
                text="Done!",
                parts=[{"kind": "text"}],
            )
            await h._notify(event, TaskState.completed)

        mock_impl.assert_awaited_once()
        call_kw = mock_impl.call_args.kwargs
        assert call_kw["message_id"] == "msg-001"
        assert call_kw["room_id"] == "room-001"
        assert call_kw["parts"] == [{"kind": "text"}]
