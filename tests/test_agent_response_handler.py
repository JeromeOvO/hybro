"""
Integration tests for AgentResponseHandler — parity verification.

Tests that feeding the same AgentEvent sequences through the handler
produces identical DB writes and SSE emissions for each event kind,
and that flow-control flags (skip_persist, send_processing_status) work.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from modules.agent_event import AgentEvent
from modules.agent_response_handler import AgentResponseHandler


# =============================================================================
# Fixtures
# =============================================================================


def _make_handler(*, db=None, sse=None, rmc=None):
    if db is None:
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=True)
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
    if sse is None:
        sse = MagicMock()
        sse.send_agent_token = AsyncMock()
        sse.send_agent_response = AsyncMock()
        sse.send_task_submitted = AsyncMock()
        sse.send_processing_status = AsyncMock()
        sse.send_error = AsyncMock()
    if rmc is None:
        rmc = MagicMock()
        rmc.resume_queue_from_continuation = AsyncMock(return_value=True)
    return AgentResponseHandler(db=db, sse=sse, room_message_center=rmc)


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


# =============================================================================
# Token events
# =============================================================================


class TestTokenEvent:
    @pytest.mark.asyncio
    async def test_sends_sse_token(self):
        h = _make_handler()
        event = AgentEvent(kind="token", **_base_event(), text="Hello")
        await h.handle(event)
        h._sse.send_agent_token.assert_awaited_once_with(
            room_id="room-001", message_id="msg-001",
            agent_id="agent-001", token="Hello",
        )

    @pytest.mark.asyncio
    async def test_does_not_persist(self):
        h = _make_handler()
        event = AgentEvent(kind="token", **_base_event(), text="Hi")
        await h.handle(event)
        h._db.update_task_state_on_message.assert_not_awaited()


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
        h._sse.send_agent_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skip_persist(self):
        h = _make_handler()
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            text="chunk", skip_persist=True,
        )
        await h.handle(event)
        h._db.accumulate_artifact_on_message.assert_not_awaited()
        h._sse.send_agent_token.assert_awaited_once()

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

    @pytest.mark.asyncio
    async def test_no_artifacts_skips_persist(self):
        h = _make_handler()
        event = AgentEvent(
            kind="artifact_update", **_base_event(),
            text="chunk", artifacts=None,
        )
        await h.handle(event)
        h._db.accumulate_artifact_on_message.assert_not_awaited()
        h._sse.send_agent_token.assert_awaited_once()


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
                "modules.agent_response_handler.AgentResponseHandler._notify",
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
                "modules.agent_response_handler.AgentResponseHandler._notify",
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
                "modules.agent_response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._sse.send_agent_response.assert_awaited_once()


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
                "modules.agent_response_handler.AgentResponseHandler._notify",
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
                "modules.agent_response_handler.AgentResponseHandler._notify",
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
                "modules.agent_response_handler.AgentResponseHandler._notify",
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
                "modules.agent_response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._db.update_task_state_on_message.assert_awaited_once_with(
            "msg-001", "input-required",
            message_text="need input", task_id="t-1", context_id="c-1",
        )
        h._rmc.resume_queue_from_continuation.assert_not_awaited()


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
    async def test_sends_token_for_text(self):
        h = _make_handler()
        event = AgentEvent(
            kind="status_update", **_base_event(), text="still working",
        )
        await h.handle(event)
        h._sse.send_agent_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_sse_for_empty_text(self):
        h = _make_handler()
        event = AgentEvent(
            kind="status_update", **_base_event(), text="",
        )
        await h.handle(event)
        h._sse.send_agent_token.assert_not_awaited()


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
            "room-001", "completed", message_id="msg-001", details="all done",
        )


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
                "modules.agent_response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            # Should not raise despite resume failure
            await h.handle(event)
