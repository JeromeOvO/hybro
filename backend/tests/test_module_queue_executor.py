"""
Unit tests for QueueExecutor module.

Tests cover:
- _check_rate_limit: allowed vs rate-limited
- QueueResult enum values
- _managed_queue cleanup behavior (RAII)
"""

import asyncio
import inspect
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from a2a_adapter.task_status import coerce_task_state
from common.dto import MessageCommitted
from common.utils.cancellation import CancellationToken
from execution.orchestration.queue_executor import (
    QueueExecutor,
    QueueProcessingResult,
    QueueResult,
)
from models.processing import ProcessingResult, ProcessingStatus
from models.room import MessageContent, RoomUserMessage


class RecordingEventPublisher:
    def __init__(self):
        self.internal_events = []

    async def publish(
        self,
        event,
        *,
        wait_for_handlers: bool = False,
        fanout: bool = True,
    ):
        self.internal_events.append(event)


# =============================================================================
# QueueResult Tests
# =============================================================================


class TestQueueResult:
    def test_enum_values(self):
        assert QueueResult.COMPLETED == "completed"
        assert QueueResult.CANCELED == "canceled"
        assert QueueResult.PAUSED == "paused"


def test_resume_from_continuation_has_no_imperative_terminal_cleanup_callback():
    parameters = inspect.signature(QueueExecutor.resume_from_continuation).parameters

    assert "before_terminal_failure" not in parameters


def test_constructor_memory_reader_is_typed_as_room_memory_reader():
    annotation = inspect.signature(QueueExecutor).parameters["memory_reader"].annotation

    assert annotation == "RoomMemoryReader"


def test_constructor_requires_internal_event_publisher():
    deps = {
        "tsm": MagicMock(),
        "delivery": MagicMock(),
        "cancellation_control": MagicMock(),
        "room_runtime": MagicMock(),
        "internal_event_publisher": None,
        "message_reader": MagicMock(),
        "message_writer": MagicMock(),
        "task_state_store": MagicMock(),
        "continuation_store": MagicMock(),
        "agent_lookup": MagicMock(),
        "room_reader": MagicMock(),
        "memory_reader": MagicMock(),
        "debate_prompt_injector": MagicMock(),
        "rate_limit_service": MagicMock(),
        "agent_dispatcher": MagicMock(),
        "agent_message_processor": MagicMock(),
        "response_handler": MagicMock(),
    }

    with pytest.raises(RuntimeError, match="internal_event_publisher"):
        QueueExecutor(**deps)


# =============================================================================
# _check_rate_limit Tests
# =============================================================================


def _make_queue_executor():
    qe = object.__new__(QueueExecutor)
    qe.rate_limit_service = MagicMock()
    qe.delivery = MagicMock()
    qe.cancellation_control = MagicMock()
    qe.cancellation_control.check_cancelled = AsyncMock(return_value=False)
    qe.cancellation_control.create_token.side_effect = lambda message_id: (
        CancellationToken(message_id=message_id)
    )
    qe.tsm = MagicMock()
    qe.message_reader = MagicMock()
    qe.message_writer = MagicMock()
    qe.task_state_store = MagicMock()
    qe.continuation_store = MagicMock()
    qe.continuation_store.save_continuation_on_message = AsyncMock(return_value=True)
    qe.agent_lookup = MagicMock()
    qe.room_reader = MagicMock()
    qe.memory_reader = MagicMock()
    qe.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=None
    )
    qe.message_reader.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    qe.message_writer.add_room_agent_message = AsyncMock(return_value=True)
    qe.message_writer.update_room_agent_message_with_new_message_content_by_message_id = AsyncMock()
    qe.task_state_store.resolve_client_request_id_for_message_id = AsyncMock(
        return_value=None
    )
    qe.delivery.send_task_submitted = AsyncMock()
    qe.delivery.send_task_update = AsyncMock()
    qe.room_runtime = MagicMock()
    qe.internal_event_publisher = RecordingEventPublisher()
    qe.agent_dispatcher = MagicMock()
    qe._agent_message_processor = MagicMock()
    qe.response_handler = MagicMock()
    qe.response_handler.notify_task_update = AsyncMock(return_value=True)
    qe.hitl_coordinator = MagicMock()
    return qe


@pytest.mark.asyncio
async def test_system_message_unacknowledged_write_retries_then_refuses_dispatch():
    qe = _make_queue_executor()
    qe.message_writer.add_room_agent_message = AsyncMock(return_value=False)
    qe.room_runtime.create_agent_message.return_value = MagicMock()
    qe.agent_lookup.get_agent_by_agent_id = AsyncMock()
    message = MagicMock(message_id="agent-msg-1")

    result = await qe.process_queue(
        deque([message]),
        "room-1",
        "root-1",
    )

    assert result.result == QueueResult.FAILED
    assert result.system_message_id is None
    assert result.error_code == "system_task_persistence_failed"
    assert qe.message_writer.add_room_agent_message.await_count == 3
    qe.agent_lookup.get_agent_by_agent_id.assert_not_awaited()
    qe.delivery.send_task_submitted.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_agent_defers_child_db_and_sse_to_root_projection():
    qe = _make_queue_executor()
    message = MagicMock(message_id="msg-1", agent_id="missing-agent")
    qe.agent_lookup.get_agent_by_agent_id = AsyncMock(return_value=None)
    qe.tsm.transition_task = AsyncMock()
    qe.response_handler.notify_task_update = AsyncMock()

    assert await qe._resolve_agent_for_message(message, "room-1") is None
    qe.tsm.transition_task.assert_not_awaited()
    qe.response_handler.notify_task_update.assert_not_awaited()


class TestCheckRateLimit:
    @pytest.mark.asyncio
    async def test_returns_false_when_allowed(self):
        qe = _make_queue_executor()
        result = MagicMock()
        result.allowed = True
        qe.rate_limit_service.check_rate_limit = AsyncMock(return_value=result)

        agent = MagicMock()
        agent.agent_id = "a1"
        agent.rate_limit_per_user_per_hour = 100
        agent.rate_limit_system_per_hour = 1000

        msg = MagicMock()
        is_limited = await qe._check_rate_limit(msg, agent, "room-1", "umsg-1", "u1")
        assert is_limited is False

    @pytest.mark.asyncio
    async def test_rate_limit_defers_child_state_and_sse_to_root_projection(self):
        qe = _make_queue_executor()
        result = MagicMock()
        result.allowed = False
        result.reason = "Too many requests"
        result.retry_after_seconds = 60
        result.user_requests_used = 100
        result.user_requests_limit = 100
        result.system_requests_used = 500
        result.system_requests_limit = 1000
        qe.rate_limit_service.check_rate_limit = AsyncMock(return_value=result)
        qe.delivery.send_rate_limit_error = AsyncMock()
        qe.tsm.transition_task = AsyncMock()

        agent = MagicMock()
        agent.agent_id = "a1"
        agent.rate_limit_per_user_per_hour = 100
        agent.rate_limit_system_per_hour = 1000

        msg = MagicMock()
        is_limited = await qe._check_rate_limit(msg, agent, "room-1", "umsg-1", "u1")

        assert is_limited is True
        qe.delivery.send_rate_limit_error.assert_not_awaited()
        qe.tsm.transition_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passes_correct_rate_limit_params(self):
        qe = _make_queue_executor()
        result = MagicMock()
        result.allowed = True
        qe.rate_limit_service.check_rate_limit = AsyncMock(return_value=result)

        agent = MagicMock()
        agent.agent_id = "agent-x"
        agent.rate_limit_per_user_per_hour = 50
        agent.rate_limit_system_per_hour = 500

        msg = MagicMock()
        await qe._check_rate_limit(msg, agent, "room-1", "umsg-1", "user-42")

        qe.rate_limit_service.check_rate_limit.assert_called_once_with(
            agent_id="agent-x",
            user_id="user-42",
            rate_limit_per_user=50,
            rate_limit_system=500,
        )


# =============================================================================
# TestProcessQueue — process_queue, single-message dispatch, continuation
# =============================================================================


@pytest.mark.asyncio
async def test_invalid_continuation_releases_paused_token():
    qe = _make_queue_executor()
    token = CancellationToken(message_id="user-1")
    qe.cancellation_control.create_token.side_effect = None
    qe.cancellation_control.create_token.return_value = token
    qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
        return_value={"remaining_queue": [], "user_message_id": "user-1"}
    )

    result = await qe.resume_from_continuation("agent-1")

    assert result.success is False
    qe.continuation_store.save_continuation_on_message.assert_awaited_once_with(
        "agent-1", {"remaining_queue": [], "user_message_id": "user-1"}
    )
    qe.cancellation_control.release_token.assert_called_once_with("user-1", token)


@pytest.mark.asyncio
async def test_missing_user_id_restores_destructively_claimed_continuation():
    qe = _make_queue_executor()
    continuation = {"remaining_queue": [], "room_id": "room-1"}
    qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
        return_value=continuation
    )
    qe.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=SimpleNamespace(related_message_id="user-1")
    )

    result = await qe.resume_from_continuation("agent-1")

    assert result.success is False
    qe.continuation_store.save_continuation_on_message.assert_awaited_once_with(
        "agent-1", continuation
    )


@pytest.mark.asyncio
async def test_missing_user_message_restores_destructively_claimed_continuation():
    qe = _make_queue_executor()
    token = CancellationToken(message_id="user-1")
    qe.cancellation_control.create_token.side_effect = None
    qe.cancellation_control.create_token.return_value = token
    continuation = {
        "remaining_queue": [],
        "room_id": "room-1",
        "user_message_id": "user-1",
    }
    qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
        return_value=continuation
    )
    qe.message_reader.get_room_user_message_by_message_id = AsyncMock(return_value=None)

    result = await qe.resume_from_continuation("agent-1")

    assert result.success is False
    qe.continuation_store.save_continuation_on_message.assert_awaited_once_with(
        "agent-1", continuation
    )
    qe.cancellation_control.release_token.assert_called_once_with("user-1", token)


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining_queue", ["not-a-list", [None]])
async def test_malformed_remaining_queue_restores_destructive_claim(remaining_queue):
    qe = _make_queue_executor()
    token = CancellationToken(message_id="user-1")
    qe.cancellation_control.create_token.side_effect = None
    qe.cancellation_control.create_token.return_value = token
    continuation = {
        "remaining_queue": remaining_queue,
        "room_id": "room-1",
        "user_message_id": "user-1",
    }
    qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
        return_value=continuation
    )

    result = await qe.resume_from_continuation("agent-1")

    assert result.success is False
    qe.continuation_store.save_continuation_on_message.assert_awaited_once_with(
        "agent-1", continuation
    )
    qe.cancellation_control.release_token.assert_called_once_with("user-1", token)
    qe.message_reader.get_room_user_message_by_message_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_queue_restore_failure_terminalizes_root_in_finally_path():
    qe = _make_queue_executor()
    token = CancellationToken(message_id="user-1")
    qe.cancellation_control.create_token.side_effect = None
    qe.cancellation_control.create_token.return_value = token
    continuation = {
        "remaining_queue": {"unexpected": "mapping"},
        "room_id": "room-1",
        "user_message_id": "user-1",
    }
    qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
        return_value=continuation
    )
    qe.continuation_store.save_continuation_on_message = AsyncMock(return_value=False)
    emit = AsyncMock(return_value={"event_id": "terminal-fact"})
    qe.bind_execution_event_deps(emit)

    result = await qe.resume_from_continuation("agent-1")

    assert result.success is False
    emit.assert_awaited_once()
    assert emit.await_args.kwargs["message_id"] == "user-1"
    qe.cancellation_control.release_token.assert_called_once_with("user-1", token)


@pytest.mark.asyncio
async def test_failed_continuation_restore_commits_reliable_root_terminal():
    qe = _make_queue_executor()
    token = CancellationToken(message_id="user-1")
    qe.cancellation_control.create_token.side_effect = None
    qe.cancellation_control.create_token.return_value = token
    continuation = {
        "remaining_queue": [],
        "room_id": "room-1",
        "user_message_id": "user-1",
    }
    qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
        return_value=continuation
    )
    qe.continuation_store.save_continuation_on_message = AsyncMock(return_value=False)
    qe.message_reader.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    emit = AsyncMock(return_value={"event_id": "terminal-fact"})
    qe.bind_execution_event_deps(emit)

    result = await qe.resume_from_continuation("agent-1")

    assert result.success is False
    emit.assert_awaited_once()
    assert emit.await_args.kwargs["message_id"] == "user-1"
    assert emit.await_args.kwargs["system_message_id"] == "sys-user-1"


@pytest.mark.asyncio
async def test_resume_quote_failure_releases_paused_token():
    qe = _make_queue_executor()
    token = CancellationToken(message_id="user-1")
    qe.cancellation_control.create_token.side_effect = None
    qe.cancellation_control.create_token.return_value = token
    qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
        return_value={
            "remaining_queue": [],
            "room_id": "room-1",
            "user_message_id": "user-1",
        }
    )
    qe.message_reader.get_room_user_message_by_message_id = AsyncMock(
        return_value=RoomUserMessage(
            room_id="room-1",
            message_id="user-1",
            message_content=MessageContent(message_text="question"),
            extend_info={"quote_id": "missing-quote"},
        )
    )
    qe.message_reader.get_quoted_snippet_by_id = AsyncMock(return_value=None)

    result = await qe.resume_from_continuation("agent-1")

    assert result.success is False
    qe.continuation_store.save_continuation_on_message.assert_awaited_once_with(
        "agent-1",
        {
            "remaining_queue": [],
            "room_id": "room-1",
            "user_message_id": "user-1",
        },
    )
    qe.cancellation_control.release_token.assert_called_once_with("user-1", token)


@pytest.mark.asyncio
async def test_empty_continuation_transfers_paused_token_to_completion_owner():
    qe = _make_queue_executor()
    token = CancellationToken(message_id="user-1")
    qe.cancellation_control.create_token.side_effect = None
    qe.cancellation_control.create_token.return_value = token
    qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
        return_value={
            "remaining_queue": [],
            "room_id": "room-1",
            "user_message_id": "user-1",
        }
    )
    qe.message_reader.get_room_user_message_by_message_id = AsyncMock(
        return_value=RoomUserMessage(
            room_id="room-1",
            message_id="user-1",
            message_content=MessageContent(message_text="question"),
            extend_info={},
        )
    )

    result = await qe.resume_from_continuation("agent-1")

    assert result.success is True
    assert result.needs_completion is True
    assert result.token is token
    qe.cancellation_control.release_token.assert_not_called()


@pytest.mark.asyncio
async def test_system_task_completion_is_compensated_when_cancel_arrives_during_send():
    qe = _make_queue_executor()
    token = CancellationToken(message_id="user-1")
    send_entered = asyncio.Event()
    release_send = asyncio.Event()
    statuses = []

    async def send_task_update(*, status, **_kwargs):
        statuses.append(status)
        if status == "completed":
            send_entered.set()
            await release_send.wait()

    db_msg = SimpleNamespace(
        message_id="sys-user-1",
        message_content=SimpleNamespace(
            message_task=SimpleNamespace(
                status=SimpleNamespace(state=coerce_task_state("working"))
            )
        ),
    )
    qe.delivery.send_task_update = send_task_update
    qe.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=db_msg
    )
    qe.message_writer.update_room_agent_message_with_new_message_content_by_message_id = AsyncMock()

    terminalizing = asyncio.create_task(
        qe._terminalize_system_task(
            room_id="room-1",
            sys_message_id="sys-user-1",
            task_status="completed",
            token=token,
        )
    )
    await send_entered.wait()
    token.cancel()
    release_send.set()

    assert await terminalizing == "canceled"
    assert statuses == ["completed", "canceled"]
    assert (
        str(getattr(db_msg.message_content.message_task.status.state, "value", ""))
        == "canceled"
    )


@pytest.mark.asyncio
async def test_system_task_terminal_persists_before_sse():
    qe = _make_queue_executor()
    order: list[str] = []
    db_msg = SimpleNamespace(
        message_id="sys-user-1",
        message_content=SimpleNamespace(
            message_task=SimpleNamespace(
                status=SimpleNamespace(state=coerce_task_state("working"))
            )
        ),
    )
    qe.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=db_msg
    )

    async def persist(*_args):
        order.append("persist")
        return True

    async def emit(**_kwargs):
        order.append("sse")

    qe.message_writer.update_room_agent_message_with_new_message_content_by_message_id = AsyncMock(
        side_effect=persist
    )
    qe.delivery.send_task_update = AsyncMock(side_effect=emit)

    assert (
        await qe._terminalize_system_task(
            room_id="room-1",
            sys_message_id="sys-user-1",
            task_status="completed",
            token=None,
        )
        == "completed"
    )
    assert order == ["persist", "sse"]


@pytest.mark.asyncio
async def test_system_task_terminal_persistence_false_retries_without_sse():
    qe = _make_queue_executor()
    db_msg = SimpleNamespace(
        message_id="sys-user-1",
        message_content=SimpleNamespace(
            message_task=SimpleNamespace(
                status=SimpleNamespace(state=coerce_task_state("working"))
            )
        ),
    )
    qe.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=db_msg
    )
    qe.message_writer.update_room_agent_message_with_new_message_content_by_message_id = AsyncMock(
        return_value=False
    )
    qe.delivery.send_task_update = AsyncMock()

    with pytest.raises(RuntimeError, match="failed to persist system task"):
        await qe._terminalize_system_task(
            room_id="room-1",
            sys_message_id="sys-user-1",
            task_status="completed",
            token=None,
        )

    assert (
        qe.message_writer.update_room_agent_message_with_new_message_content_by_message_id.await_count
        == 3
    )
    qe.delivery.send_task_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_task_terminal_persistence_exception_retries_without_sse():
    qe = _make_queue_executor()
    db_msg = SimpleNamespace(
        message_id="sys-user-1",
        message_content=SimpleNamespace(
            message_task=SimpleNamespace(
                status=SimpleNamespace(state=coerce_task_state("working"))
            )
        ),
    )
    qe.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=db_msg
    )
    qe.message_writer.update_room_agent_message_with_new_message_content_by_message_id = AsyncMock(
        side_effect=RuntimeError("database unavailable")
    )
    qe.delivery.send_task_update = AsyncMock()

    with pytest.raises(RuntimeError, match="database unavailable"):
        await qe._terminalize_system_task(
            room_id="room-1",
            sys_message_id="sys-user-1",
            task_status="failed",
            token=None,
        )

    assert (
        qe.message_writer.update_room_agent_message_with_new_message_content_by_message_id.await_count
        == 3
    )
    qe.delivery.send_task_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_empty_continuation_does_not_request_completion():
    qe = _make_queue_executor()
    token = CancellationToken(message_id="user-1")
    qe.cancellation_control.create_token.side_effect = None
    qe.cancellation_control.create_token.return_value = token

    async def hydrate_cancellation(_message_id):
        token.cancel()
        return True

    qe.cancellation_control.check_cancelled.side_effect = hydrate_cancellation
    qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
        return_value={
            "remaining_queue": [],
            "room_id": "room-1",
            "user_message_id": "user-1",
        }
    )

    result = await qe.resume_from_continuation("agent-1")

    assert result.success is True
    assert result.needs_completion is False
    qe.cancellation_control.release_token.assert_called_once_with("user-1", token)


class TestProcessQueue:
    @pytest.mark.asyncio
    async def test_process_single_message_delegates_to_amp(self):
        """_process_single_message delegates to AgentMessageProcessor."""
        qe = _make_queue_executor()

        msg = MagicMock()
        msg.message_id = "msg-1"
        msg.user_id = "u1"

        agent = MagicMock()
        agent.agent_card = MagicMock()

        qe._agent_message_processor.process_single_message = AsyncMock(
            return_value=ProcessingResult(ProcessingStatus.SUCCESS, "reply")
        )

        result = await qe._process_single_message(msg, "room-1", agent, "umsg-1")

        qe._agent_message_processor.process_single_message.assert_awaited_once_with(
            msg,
            "room-1",
            agent,
            "umsg-1",
            token=None,
            step_number=None,
            total_steps=None,
            quoted_text=None,
        )
        assert result.status == ProcessingStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_process_queue_completes_all_messages(self):
        """Two-item queue where both succeed -> QueueResult.COMPLETED."""
        qe = _make_queue_executor()

        msg1 = MagicMock(
            message_id="msg-1",
            step_number=1,
            total_steps=2,
            extend_info=None,
            agent_id="a1",
            user_id="u1",
        )
        msg2 = MagicMock(
            message_id="msg-2",
            step_number=2,
            total_steps=2,
            extend_info=None,
            agent_id="a1",
            user_id="u1",
        )

        queue = deque([msg1, msg2])

        agent = MagicMock()
        agent.agent_id = "a1"
        agent.agent_card = MagicMock()
        agent.agent_card.name = "TestAgent"

        qe._resolve_agent_for_message = AsyncMock(return_value=agent)
        qe._process_single_message = AsyncMock(
            return_value=ProcessingResult(ProcessingStatus.SUCCESS, "ok")
        )
        qe._queue_next_messages = AsyncMock()
        qe.message_writer.cancel_descendants = AsyncMock()

        result = await qe.process_queue(queue, "room-1", "umsg-1")

        assert result.result == QueueResult.COMPLETED
        assert qe._process_single_message.call_count == 2

    @pytest.mark.asyncio
    async def test_preflight_failure_defers_child_mutation_to_root_projection(self):
        qe = _make_queue_executor()
        msg = MagicMock(
            message_id="msg-1",
            step_number=1,
            total_steps=1,
            extend_info={
                "attachment_preflight_failure": {
                    "code": "file_too_large",
                    "message": "Attached file report.pdf exceeds the inline A2A limit.",
                }
            },
            agent_id="a1",
            user_id="u1",
            turn_id=None,
        )
        queue = deque([msg])
        agent = MagicMock()
        agent.agent_id = "a1"
        agent.agent_card = MagicMock()
        agent.agent_card.name = "TestAgent"
        qe._resolve_agent_for_message = AsyncMock(return_value=agent)
        qe._process_single_message = AsyncMock(
            return_value=ProcessingResult(
                ProcessingStatus.FAILED,
                response_text="Attached file report.pdf exceeds the inline A2A limit.",
                status_message="file_too_large",
            )
        )
        qe.tsm.fail_pre_dispatch_task = AsyncMock()
        qe.response_handler.notify_task_update = AsyncMock()
        qe.message_writer.cancel_descendants = AsyncMock()
        qe._terminalize_system_task = AsyncMock(return_value="failed")

        result = await qe.process_queue(queue, "room-1", "umsg-1")

        assert result.result == QueueResult.FAILED
        assert result.error_message == (
            "Attached file report.pdf exceeds the inline A2A limit."
        )
        assert result.error_code == "file_too_large"
        qe.tsm.fail_pre_dispatch_task.assert_not_awaited()
        qe.response_handler.notify_task_update.assert_not_awaited()
        qe.message_writer.cancel_descendants.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_queue_generic_failed_result_does_not_create_preflight_task(
        self,
    ):
        qe = _make_queue_executor()
        msg = MagicMock(
            message_id="msg-1",
            step_number=1,
            total_steps=1,
            extend_info=None,
            agent_id="a1",
            user_id="u1",
            turn_id=None,
        )
        queue = deque([msg])
        agent = MagicMock()
        agent.agent_id = "a1"
        agent.agent_card = MagicMock()
        agent.agent_card.name = "TestAgent"
        qe._resolve_agent_for_message = AsyncMock(return_value=agent)
        qe._process_single_message = AsyncMock(
            return_value=ProcessingResult(
                ProcessingStatus.FAILED,
                response_text="Agent processing failed downstream.",
                status_message=None,
            )
        )
        qe.tsm.fail_pre_dispatch_task = AsyncMock()
        qe.response_handler.notify_task_update = AsyncMock()
        qe.message_writer.cancel_descendants = AsyncMock()
        qe._terminalize_system_task = AsyncMock(return_value="failed")

        result = await qe.process_queue(queue, "room-1", "umsg-1")

        assert result.result == QueueResult.FAILED
        qe.tsm.fail_pre_dispatch_task.assert_not_awaited()
        qe.response_handler.notify_task_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_queue_submits_system_hybro_task_through_focused_ports(self):
        qe = _make_queue_executor()
        qe.message_reader.get_room_agent_message_by_message_id = AsyncMock(
            side_effect=[None, None]
        )
        sys_msg = SimpleNamespace(message_id=None)
        qe.room_runtime.create_agent_message.return_value = sys_msg

        msg = MagicMock(
            message_id="msg-1",
            step_number=1,
            total_steps=1,
            extend_info=None,
            agent_id="a1",
            user_id="u1",
        )
        queue = deque([msg])

        agent = MagicMock()
        agent.agent_id = "a1"
        agent.agent_card = MagicMock()
        agent.agent_card.name = "TestAgent"

        qe._resolve_agent_for_message = AsyncMock(return_value=agent)
        qe._process_single_message = AsyncMock(
            return_value=ProcessingResult(ProcessingStatus.SUCCESS)
        )
        qe._queue_next_messages = AsyncMock()

        result = await qe.process_queue(queue, "room-1", "umsg-1")

        assert result.result == QueueResult.COMPLETED
        assert qe.message_reader.get_room_agent_message_by_message_id.await_args_list[
            0
        ] == call("sys-umsg-1")
        qe.message_writer.add_room_agent_message.assert_awaited_once_with(sys_msg)
        qe.delivery.send_task_submitted.assert_awaited_once()
        assert (
            qe.delivery.send_task_submitted.await_args.kwargs["message_id"]
            == "sys-umsg-1"
        )

    @pytest.mark.asyncio
    async def test_process_queue_delays_completed_system_task_until_root_cas(self):
        qe = _make_queue_executor()
        system_content = SimpleNamespace(
            message_task=SimpleNamespace(status=SimpleNamespace(state=None))
        )
        system_msg = SimpleNamespace(
            message_id="sys-umsg-1",
            message_content=system_content,
        )
        qe.message_reader.get_room_agent_message_by_message_id = AsyncMock(
            side_effect=[system_msg, system_msg]
        )

        msg = MagicMock(
            message_id="msg-1",
            step_number=1,
            total_steps=1,
            extend_info=None,
            agent_id="a1",
            user_id="u1",
        )
        queue = deque([msg])

        agent = MagicMock()
        agent.agent_id = "a1"
        agent.agent_card = MagicMock()
        agent.agent_card.name = "TestAgent"

        qe._resolve_agent_for_message = AsyncMock(return_value=agent)
        qe._process_single_message = AsyncMock(
            return_value=ProcessingResult(ProcessingStatus.SUCCESS)
        )
        qe._queue_next_messages = AsyncMock()

        result = await qe.process_queue(queue, "room-1", "umsg-1")

        assert result.result == QueueResult.COMPLETED
        qe.delivery.send_task_update.assert_not_awaited()
        assert (
            qe.message_reader.get_room_agent_message_by_message_id.await_args_list
            == [call("sys-umsg-1")]
        )
        update_content = qe.message_writer.update_room_agent_message_with_new_message_content_by_message_id
        update_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_queue_cancels_on_cancellation_token(self):
        """Pre-cancelled token -> QueueResult.CANCELED on the first iteration."""
        qe = _make_queue_executor()
        order: list[str] = []

        msg = MagicMock(
            message_id="msg-1",
            step_number=1,
            total_steps=1,
            extend_info=None,
        )

        queue = deque([msg])

        token = CancellationToken(message_id="umsg-1")
        token.cancel()

        qe.tsm.transition_task = AsyncMock()
        qe.cancellation_control.clear_cancellation = MagicMock()
        qe.message_writer.cancel_descendants = AsyncMock()
        qe._terminalize_system_task = AsyncMock(return_value="canceled")

        async def emit_accepted(*_args, **_kwargs):
            order.append("emit")
            return {"event_id": "canceled-winner"}

        emit = AsyncMock(side_effect=emit_accepted)
        qe.bind_execution_event_deps(emit)

        result = await qe.process_queue(queue, "room-1", "umsg-1", token=token)

        assert result.result == QueueResult.CANCELED
        qe.tsm.transition_task.assert_not_awaited()
        qe.message_writer.cancel_descendants.assert_not_awaited()
        emit.assert_awaited_once()
        qe.delivery.send_processing_status.assert_not_called()
        assert order == ["emit"]
        qe.cancellation_control.clear_cancellation.assert_called_once_with("umsg-1")

    @pytest.mark.asyncio
    async def test_process_queue_records_before_awaiting_input_send(self):
        """HITL AWAITING_INPUT records before the frontend pause status."""
        qe = _make_queue_executor()
        order: list[str] = []
        private_prompt = "PRIVATE_SENTINEL_queue_remote_prompt"

        msg = MagicMock(
            message_id="msg-1",
            step_number=1,
            total_steps=1,
            extend_info=None,
            agent_id="a1",
            user_id="u1",
        )
        queue = deque([msg])
        agent = MagicMock()
        agent.agent_id = "a1"
        agent.agent_card = MagicMock()
        agent.agent_card.name = "Agent"

        qe._resolve_agent_for_message = AsyncMock(return_value=agent)
        qe._process_single_message = AsyncMock(
            return_value=ProcessingResult(
                ProcessingStatus.AWAITING_INPUT,
                message_id="paused-msg",
                status_message=private_prompt,
            )
        )
        qe._queue_next_messages = AsyncMock()
        qe._save_continuation = AsyncMock()
        qe.message_writer.cancel_descendants = AsyncMock()
        emit = AsyncMock(side_effect=lambda *a, **k: order.append("emit"))
        qe.bind_execution_event_deps(emit)
        hitl_service = MagicMock()
        hitl_service.request_input = AsyncMock(
            return_value=SimpleNamespace(request_id="hitl-1")
        )

        qe.hitl_coordinator = hitl_service
        result = await qe.process_queue(queue, "room-1", "umsg-1")

        assert result.result == QueueResult.PAUSED
        hitl_service.request_input.assert_awaited_once()
        prompt = hitl_service.request_input.await_args.kwargs["prompt"]
        assert prompt == "The agent needs additional information."
        assert private_prompt not in repr(hitl_service.request_input.await_args.kwargs)
        emit.assert_awaited_once()
        qe.delivery.send_processing_status.assert_not_called()
        assert order == ["emit"]

    @pytest.mark.asyncio
    async def test_queue_cancellation_commits_root_before_token_cleanup(self):
        """No child mutation occurs before or after the durable root CAS."""
        qe = _make_queue_executor()
        order: list[str] = []

        msg = MagicMock(
            message_id="msg-1",
            step_number=1,
            total_steps=1,
            extend_info=None,
        )
        queue = deque([msg])
        token = CancellationToken(message_id="umsg-1")
        token.cancel()

        qe.tsm.transition_task = AsyncMock(
            side_effect=lambda *a, **k: order.append("cancel-task")
        )
        qe.message_writer.cancel_descendants = AsyncMock(
            side_effect=lambda *a, **k: order.append("cancel-descendants")
        )
        qe.cancellation_control.clear_cancellation = MagicMock(
            side_effect=lambda *a, **k: order.append("clear-token")
        )
        qe._terminalize_system_task = AsyncMock(return_value="canceled")

        async def emit_accepted(*_args, **_kwargs):
            order.append("emit")
            return {"event_id": "canceled-winner"}

        emit = AsyncMock(side_effect=emit_accepted)
        qe.bind_execution_event_deps(emit)

        result = await qe.process_queue(queue, "room-1", "umsg-1", token=token)

        assert result.result == QueueResult.CANCELED
        assert order == ["emit", "clear-token"]
        qe.tsm.transition_task.assert_not_awaited()
        qe.message_writer.cancel_descendants.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_queue_cancellation_opposing_winner_never_mutates_children(self):
        qe = _make_queue_executor()
        msg = MagicMock(
            message_id="msg-1",
            step_number=1,
            total_steps=1,
            extend_info=None,
        )
        token = CancellationToken(message_id="umsg-1")
        token.cancel()
        qe.tsm.transition_task = AsyncMock()
        qe.message_writer.cancel_descendants = AsyncMock()
        qe.cancellation_control.clear_cancellation = MagicMock()
        emit = AsyncMock(return_value=None)  # opposing canonical terminal winner
        qe.bind_execution_event_deps(emit)

        result = await qe.process_queue(deque([msg]), "room-1", "umsg-1", token=token)

        assert result.result == QueueResult.CANCELED
        emit.assert_awaited_once()
        qe.tsm.transition_task.assert_not_awaited()
        qe.message_writer.cancel_descendants.assert_not_awaited()
        qe.cancellation_control.clear_cancellation.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_from_continuation_failure_uses_durable_root_projection(self):
        """Queue resume failure has no imperative child-cleanup callback."""
        qe = _make_queue_executor()
        order: list[str] = []
        continuation = {
            "remaining_queue": [
                {
                    "message_id": "msg-2",
                    "room_id": "room-1",
                    "message_type": "agent",
                    "message_content": {"message_text": "work"},
                }
            ],
            "room_id": "room-1",
            "user_message_id": "umsg-1",
            "request_user_id": "u1",
        }
        qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
            return_value=continuation
        )
        qe.cancellation_control.get_token = MagicMock(return_value=None)
        qe.cancellation_control.create_token = MagicMock(
            return_value=CancellationToken(message_id="umsg-1")
        )
        qe.process_queue = AsyncMock(
            return_value=QueueProcessingResult(result=QueueResult.FAILED)
        )
        qe.message_reader.get_room_user_message_by_message_id = AsyncMock(
            return_value=RoomUserMessage(
                room_id="room-1",
                message_id="umsg-1",
                message_content=MessageContent(message_text="question"),
                extend_info={},
            )
        )
        emit = AsyncMock(side_effect=lambda *a, **k: order.append("emit"))
        qe.bind_execution_event_deps(emit)
        result = await qe.resume_from_continuation("paused-msg")

        assert result.success is False
        assert order == ["emit"]
        assert emit.await_args.kwargs["system_message_id"] is None
        assert emit.await_args.kwargs["turn_event_enabled"] is False

    @pytest.mark.asyncio
    async def test_resume_failure_commits_terminal_projection_intent(self):
        qe = _make_queue_executor()
        continuation = {
            "remaining_queue": [
                {
                    "message_id": "msg-2",
                    "room_id": "room-1",
                    "message_type": "agent",
                    "message_content": {"message_text": "work"},
                }
            ],
            "room_id": "room-1",
            "user_message_id": "umsg-1",
        }
        qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
            return_value=continuation
        )
        qe.cancellation_control.get_token = MagicMock(return_value=None)
        qe.cancellation_control.create_token = MagicMock(
            return_value=CancellationToken(message_id="umsg-1")
        )
        qe.process_queue = AsyncMock(
            return_value=QueueProcessingResult(
                result=QueueResult.FAILED,
                system_message_id="sys-umsg-1",
            )
        )
        qe.message_reader.get_room_user_message_by_message_id = AsyncMock(
            return_value=RoomUserMessage(
                room_id="room-1",
                message_id="umsg-1",
                message_content=MessageContent(message_text="question"),
                extend_info={},
            )
        )
        emit = AsyncMock(return_value={"event_id": "terminal-fact"})
        qe.bind_execution_event_deps(emit)

        result = await qe.resume_from_continuation("paused-msg")

        assert result.success is False
        emit.assert_awaited_once()
        assert emit.await_args.kwargs["system_message_id"] == "sys-umsg-1"

    @pytest.mark.asyncio
    async def test_save_continuation_persists_to_db(self):
        """_save_continuation serializes the queue and writes via continuation_store."""
        qe = _make_queue_executor()

        remaining = MagicMock()
        remaining.model_dump = MagicMock(
            return_value={"message_id": "msg-2", "room_id": "room-1"}
        )

        queue = deque([remaining])

        agent = MagicMock()
        agent.agent_id = "a1"
        agent.agent_card = MagicMock()
        agent.agent_card.name = "TestAgent"

        qe.continuation_store.save_continuation_on_message = AsyncMock(
            return_value=True
        )

        await qe._save_continuation(
            message_id="paused-msg",
            message_queue=queue,
            room_id="room-1",
            user_message_id="umsg-1",
            request_user_id="u1",
            current_agent=agent,
        )

        qe.continuation_store.save_continuation_on_message.assert_called_once_with(
            "paused-msg",
            {
                "remaining_queue": [{"message_id": "msg-2", "room_id": "room-1"}],
                "room_id": "room-1",
                "user_message_id": "umsg-1",
                "request_user_id": "u1",
                "current_agent_id": "a1",
                "current_agent_name": "TestAgent",
            },
        )

    @pytest.mark.asyncio
    async def test_resume_from_continuation_restores_queue(self):
        """Saved continuation data is loaded, queue rebuilt, and process_queue invoked."""
        qe = _make_queue_executor()

        continuation = {
            "remaining_queue": [
                {
                    "message_id": "msg-2",
                    "room_id": "room-1",
                    "message_type": "agent",
                }
            ],
            "room_id": "room-1",
            "user_message_id": "umsg-1",
            "request_user_id": "u1",
            "current_agent_id": "a1",
            "current_agent_name": "TestAgent",
        }

        qe.continuation_store.get_and_clear_continuation_on_message = AsyncMock(
            return_value=continuation
        )
        qe.cancellation_control.get_token = MagicMock(return_value=None)
        qe.cancellation_control.create_token = MagicMock(
            return_value=CancellationToken(message_id="umsg-1")
        )
        qe.process_queue = AsyncMock(
            return_value=QueueProcessingResult(result=QueueResult.COMPLETED)
        )
        qe.message_reader.get_room_user_message_by_message_id = AsyncMock(
            return_value=RoomUserMessage(
                room_id="room-1",
                message_id="umsg-1",
                message_content=MessageContent(message_text="question"),
                extend_info={},
            )
        )

        with patch(
            "execution.orchestration.queue_executor.RoomAgentMessage"
        ) as MockRAM:
            MockRAM.model_validate.return_value = MagicMock(message_id="msg-2")
            result = await qe.resume_from_continuation(
                "paused-msg", task_result_text="task done"
            )

        assert result.success is True
        assert result.needs_completion is True
        assert result.room_id == "room-1"
        assert result.user_message_id == "umsg-1"
        qe.process_queue.assert_called_once()
        assert len(qe.internal_event_publisher.internal_events) == 1
        event = qe.internal_event_publisher.internal_events[0]
        assert isinstance(event, MessageCommitted)
        assert event.room_id == "room-1"
        assert event.message_id == "paused-msg"
        assert event.message_type == "agent"
        assert event.agent_id == "a1"
        assert event.agent_name == "TestAgent"
        assert event.was_successful is True
