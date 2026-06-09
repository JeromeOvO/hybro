"""
Transport parity integration tests.

Feeds the same AgentEvent sequences through AgentResponseHandler and
asserts identical DB + SSE outcomes regardless of skip_persist flag
(direct transport uses skip_persist=True, relay/webhook use False).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from execution.dispatch.agent_event import AgentEvent
from execution.dispatch.response_handler import AgentResponseHandler

# =========================================================================
# Fixtures
# =========================================================================


def _make_handler(*, db=None, sse=None, rmc=None):
    if db is None:
        db = MagicMock()
        db.update_task_state_on_message = AsyncMock(return_value=True)
        db.accumulate_artifact_on_message = AsyncMock(return_value=True)
    if sse is None:
        sse = MagicMock()
        sse.send_agent_response = AsyncMock()
        sse.send_artifact_update = AsyncMock()
        sse.send_task_submitted = AsyncMock()
        sse.send_processing_status = AsyncMock()
        sse.send_error = AsyncMock()
    if rmc is None:
        rmc = MagicMock()
        rmc.resume_queue_from_continuation = AsyncMock(return_value=True)
    return AgentResponseHandler(
        message_writer=db,
        task_writer=db,
        continuation_store=db,
        client_request_resolver=db,
        room_reader=db,
        hitl_reader=db,
        sse_manager=sse,
        room_message_center=rmc,
    )


def _base(**overrides):
    defaults = dict(
        message_id="msg-001",
        room_id="room-001",
        agent_id="agent-001",
        user_id="user-001",
        related_message_id="umsg-001",
    )
    defaults.update(overrides)
    return defaults


def _multi_event_sequence(*, skip_persist: bool) -> list[AgentEvent]:
    """artifact_update (text) -> artifact_update (text) -> artifact_update (file) -> response (terminal)."""
    return [
        AgentEvent(
            kind="artifact_update", **_base(),
            text="Hello ",
            artifacts=[{
                "artifact_id": "msg-001-stream",
                "parts": [{"kind": "text", "text": "Hello "}],
            }],
            append=True, last_chunk=False,
            skip_persist=skip_persist,
        ),
        AgentEvent(
            kind="artifact_update", **_base(),
            text="world",
            artifacts=[{
                "artifact_id": "msg-001-stream",
                "parts": [{"kind": "text", "text": "world"}],
            }],
            append=True, last_chunk=False,
            skip_persist=skip_persist,
        ),
        AgentEvent(
            kind="artifact_update", **_base(),
            text="file ready", artifacts=[{"id": "a1"}],
            skip_persist=skip_persist,
        ),
        AgentEvent(
            kind="response", **_base(),
            text="Hello world", parts=[{"kind": "text", "text": "Hello world"}],
            skip_persist=skip_persist,
        ),
    ]


# =========================================================================
# Multi-event sequence — skip_persist=False (relay/webhook path)
# =========================================================================


class TestMultiEventSequenceWithPersist:
    @pytest.mark.asyncio
    async def test_full_sequence_persists_and_emits_sse(self):
        h = _make_handler()

        with pytest.MonkeyPatch.context() as mp:
            mock_notify = AsyncMock(return_value=True)
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                mock_notify,
            )
            for event in _multi_event_sequence(skip_persist=False):
                await h.handle(event)

        # Three artifact_update events -> three send_artifact_update calls
        assert h._sse.send_artifact_update.await_count == 3

        # All three artifact_update events with artifacts trigger DB accumulation
        assert h._message_writer.accumulate_artifact_on_message.await_count == 3

        # response persists "completed" state via update_task_state_on_message
        h._task_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001", "completed",
            message_text="Hello world", artifacts=None,
        )

        # send_agent_response removed — _notify() delivers parts via task_update
        h._sse.send_agent_response.assert_not_awaited()

        # Terminal response resumes orchestration
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001", task_result_text="Hello world", failed=False,
        )


# =========================================================================
# Multi-event sequence — skip_persist=True (direct transport path)
# =========================================================================


class TestMultiEventSequenceSkipPersist:
    @pytest.mark.asyncio
    async def test_full_sequence_skips_db_but_emits_sse(self):
        h = _make_handler()

        with pytest.MonkeyPatch.context() as mp:
            mock_notify = AsyncMock(return_value=True)
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                mock_notify,
            )
            for event in _multi_event_sequence(skip_persist=True):
                await h.handle(event)

        # SSE emissions are identical regardless of skip_persist
        # Three artifact_update events (send_agent_response removed)
        assert h._sse.send_artifact_update.await_count == 3
        h._sse.send_agent_response.assert_not_awaited()

        # DB writes are skipped
        h._task_writer.update_task_state_on_message.assert_not_awaited()
        h._message_writer.accumulate_artifact_on_message.assert_not_awaited()

        # Orchestration resume still fires
        h._rmc.resume_queue_from_continuation.assert_awaited_once()


# =========================================================================
# SSE parity: both paths produce identical SSE call patterns
# =========================================================================


class TestSSEParity:
    @pytest.mark.asyncio
    async def test_sse_calls_identical_across_persist_modes(self):
        """Both skip_persist=True and False produce the same SSE emissions."""
        results = {}
        for skip in (True, False):
            h = _make_handler()
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    "execution.dispatch.response_handler.AgentResponseHandler._notify",
                    AsyncMock(return_value=True),
                )
                for event in _multi_event_sequence(skip_persist=skip):
                    await h.handle(event)

            results[skip] = {
                "artifact_count": h._sse.send_artifact_update.await_count,
                "artifact_calls": h._sse.send_artifact_update.call_args_list,
                "response_count": h._sse.send_agent_response.await_count,
                "response_calls": h._sse.send_agent_response.call_args_list,
            }

        assert results[True]["artifact_count"] == results[False]["artifact_count"]
        assert results[True]["artifact_calls"] == results[False]["artifact_calls"]
        assert results[True]["response_count"] == results[False]["response_count"]
        assert results[True]["response_calls"] == results[False]["response_calls"]


# =========================================================================
# Terminal error event parity
# =========================================================================


class TestErrorEventParity:
    @pytest.mark.asyncio
    async def test_error_persists_when_not_skipped(self):
        h = _make_handler()
        event = AgentEvent(
            kind="error", **_base(),
            error_text="agent crashed", state="failed",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._task_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001", "failed", message_text="agent crashed",
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001", task_result_text=None, failed=True,
        )

    @pytest.mark.asyncio
    async def test_error_skips_persist(self):
        h = _make_handler()
        event = AgentEvent(
            kind="error", **_base(),
            error_text="agent crashed", state="failed",
            skip_persist=True,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._task_writer.update_task_state_on_message.assert_not_awaited()
        h._rmc.resume_queue_from_continuation.assert_awaited_once()


# =========================================================================
# Terminal canceled event parity
# =========================================================================


class TestCanceledEventParity:
    @pytest.mark.asyncio
    async def test_canceled_persists_when_not_skipped(self):
        h = _make_handler()
        event = AgentEvent(kind="canceled", **_base(), text="user stopped")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._task_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001", "canceled", message_text="user stopped",
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001", task_result_text=None, failed=True,
        )

    @pytest.mark.asyncio
    async def test_canceled_skips_persist(self):
        h = _make_handler()
        event = AgentEvent(
            kind="canceled", **_base(),
            text="user stopped", skip_persist=True,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._task_writer.update_task_state_on_message.assert_not_awaited()
        h._rmc.resume_queue_from_continuation.assert_awaited_once()
