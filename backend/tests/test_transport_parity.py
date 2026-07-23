"""
Transport parity integration tests.

Feeds the same AgentEvent sequences through AgentResponseHandler and
asserts identical public delivery outcomes regardless of skip_persist flag
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
        db.update_task_state_on_message = AsyncMock(return_value=(True, None))
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
        delivery=sse,
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
    """Nonterminal artifact updates followed by a terminal response."""
    return [
        AgentEvent(
            kind="artifact_update",
            **_base(),
            text="Hello ",
            artifacts=[
                {
                    "artifact_id": "msg-001-stream",
                    "parts": [{"kind": "text", "text": "Hello "}],
                }
            ],
            append=True,
            last_chunk=False,
            skip_persist=skip_persist,
        ),
        AgentEvent(
            kind="artifact_update",
            **_base(),
            text="world",
            artifacts=[
                {
                    "artifact_id": "msg-001-stream",
                    "parts": [{"kind": "text", "text": "world"}],
                }
            ],
            append=True,
            last_chunk=False,
            skip_persist=skip_persist,
        ),
        AgentEvent(
            kind="artifact_update",
            **_base(),
            text="file ready",
            artifacts=[{"id": "a1"}],
            skip_persist=skip_persist,
        ),
        AgentEvent(
            kind="response",
            **_base(),
            text="Hello world",
            parts=[{"kind": "text", "text": "Hello world"}],
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

        # Nonterminal artifact_update events are private and produce no frames.
        h._delivery.send_artifact_update.assert_not_awaited()

        # Nonterminal artifacts are not persisted as public result shadows.
        h._message_writer.accumulate_artifact_on_message.assert_not_awaited()

        # response persists "completed" state via update_task_state_on_message
        h._task_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001",
            "completed",
            message_text="Hello world",
            artifacts=[
                {
                    "artifactId": "msg-001-response",
                    "name": "response",
                    "parts": [
                        {
                            "kind": "text",
                            "text": "Hello world",
                            "metadata": None,
                        }
                    ],
                }
            ],
        )

        # send_agent_response removed — _notify() delivers parts via task_update
        h._delivery.send_agent_response.assert_not_awaited()

        # Terminal response resumes orchestration
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text="Hello world",
            failed=False,
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

        # Public emissions are identical regardless of skip_persist:
        # nonterminal artifacts produce no public frames.
        h._delivery.send_artifact_update.assert_not_awaited()
        h._delivery.send_agent_response.assert_not_awaited()

        # DB writes are skipped
        h._task_writer.update_task_state_on_message.assert_not_awaited()
        h._message_writer.accumulate_artifact_on_message.assert_not_awaited()

        # Orchestration resume still fires
        h._rmc.resume_queue_from_continuation.assert_awaited_once()


# =========================================================================
# Delivery parity: both paths produce identical public call patterns
# =========================================================================


class TestDeliveryParity:
    @pytest.mark.asyncio
    async def test_public_delivery_calls_identical_across_persist_modes(self):
        """Both skip_persist=True and False produce the same public emissions."""
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
                "artifact_count": h._delivery.send_artifact_update.await_count,
                "artifact_calls": h._delivery.send_artifact_update.call_args_list,
                "response_count": h._delivery.send_agent_response.await_count,
                "response_calls": h._delivery.send_agent_response.call_args_list,
            }

        assert results[True]["artifact_count"] == results[False]["artifact_count"]
        assert results[True]["artifact_count"] == 0
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
            kind="error",
            **_base(),
            error_text="agent crashed",
            state="failed",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "execution.dispatch.response_handler.AgentResponseHandler._notify",
                AsyncMock(return_value=True),
            )
            await h.handle(event)

        h._task_writer.update_task_state_on_message.assert_awaited_once_with(
            "msg-001",
            "failed",
            message_text="Task failed",
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text=None,
            failed=True,
        )

    @pytest.mark.asyncio
    async def test_error_skips_persist(self):
        h = _make_handler()
        event = AgentEvent(
            kind="error",
            **_base(),
            error_text="agent crashed",
            state="failed",
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
            "msg-001",
            "canceled",
            message_text="Task was canceled",
        )
        h._rmc.resume_queue_from_continuation.assert_awaited_once_with(
            message_id="msg-001",
            task_result_text=None,
            failed=True,
        )

    @pytest.mark.asyncio
    async def test_canceled_skips_persist(self):
        h = _make_handler()
        event = AgentEvent(
            kind="canceled",
            **_base(),
            text="user stopped",
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
