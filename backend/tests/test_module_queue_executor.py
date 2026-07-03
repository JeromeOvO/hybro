"""
Unit tests for QueueExecutor module.

Tests cover:
- _check_rate_limit: allowed vs rate-limited
- QueueResult enum values
- _managed_queue cleanup behavior (RAII)
"""

import inspect
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from a2a.types import TaskState
from a2a_adapter.task_status import coerce_task_state

from common.dto import MessageCommitted
from common.utils.cancellation import CancellationToken
from execution.orchestration.queue_executor import (
    QueueExecutor,
    QueueProcessingResult,
    QueueResult,
)
from models.processing import ProcessingResult, ProcessingStatus


class RecordingEventPublisher:
    def __init__(self):
        self.internal_events = []

    async def emit_internal(
        self,
        event,
        *,
        wait_for_local_handlers: bool = False,
        broadcast: bool = True,
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


def test_resume_from_continuation_before_terminal_failure_is_typed():
    annotation = inspect.signature(
        QueueExecutor.resume_from_continuation
    ).parameters["before_terminal_failure"].annotation

    assert annotation == "Callable[[str, str], Awaitable[None]] | None"


def test_constructor_memory_reader_is_typed_as_room_memory_reader():
    annotation = inspect.signature(QueueExecutor).parameters["memory_reader"].annotation

    assert annotation == "RoomMemoryReader"


def test_constructor_requires_event_publisher():
    deps = {
        "tsm": MagicMock(),
        "delivery": MagicMock(),
        "room_runtime": MagicMock(),
        "event_publisher": None,
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

    with pytest.raises(RuntimeError, match="event_publisher"):
        QueueExecutor(**deps)


# =============================================================================
# _check_rate_limit Tests
# =============================================================================


def _make_queue_executor():
    qe = object.__new__(QueueExecutor)
    qe.rate_limit_service = MagicMock()
    qe.delivery = MagicMock()
    qe.tsm = MagicMock()
    qe.message_reader = MagicMock()
    qe.message_writer = MagicMock()
    qe.task_state_store = MagicMock()
    qe.continuation_store = MagicMock()
    qe.agent_lookup = MagicMock()
    qe.room_reader = MagicMock()
    qe.memory_reader = MagicMock()
    qe.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=None
    )
    qe.message_reader.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    qe.message_writer.add_room_agent_message = AsyncMock()
    qe.message_writer.update_room_agent_message_with_new_message_content_by_message_id = (
        AsyncMock()
    )
    qe.task_state_store.resolve_client_request_id_for_message_id = AsyncMock(
        return_value=None
    )
    qe.delivery.send_task_submitted = AsyncMock()
    qe.delivery.send_task_update = AsyncMock()
    qe.room_runtime = MagicMock()
    qe.event_publisher = RecordingEventPublisher()
    qe.agent_dispatcher = MagicMock()
    qe._agent_message_processor = MagicMock()
    qe.response_handler = MagicMock()
    qe.response_handler.notify_task_update = AsyncMock(return_value=True)
    qe.hitl_coordinator = MagicMock()
    return qe


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
    async def test_returns_true_and_cancels_when_rate_limited(self):
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
        qe.delivery.send_rate_limit_error.assert_called_once()
        qe.tsm.transition_task.assert_called_once_with(
            msg, TaskState.canceled, persist=True
        )

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

        result = await qe._process_single_message(
            msg, "room-1", agent, "umsg-1"
        )

        qe._agent_message_processor.process_single_message.assert_awaited_once_with(
            msg, "room-1", agent, "umsg-1",
            token=None, step_number=None, total_steps=None, quoted_text=None,
        )
        assert result.status == ProcessingStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_process_queue_completes_all_messages(self):
        """Two-item queue where both succeed -> QueueResult.COMPLETED."""
        qe = _make_queue_executor()

        msg1 = MagicMock(
            message_id="msg-1", step_number=1, total_steps=2,
            extend_info=None, agent_id="a1", user_id="u1",
        )
        msg2 = MagicMock(
            message_id="msg-2", step_number=2, total_steps=2,
            extend_info=None, agent_id="a1", user_id="u1",
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
    async def test_process_queue_failed_result_persists_preflight_reason(self):
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

        result = await qe.process_queue(queue, "room-1", "umsg-1")

        assert result.result == QueueResult.FAILED
        qe.tsm.fail_pre_dispatch_task.assert_awaited_once_with(
            msg,
            error="Attached file report.pdf exceeds the inline A2A limit.",
            error_code="file_too_large",
        )
        qe.response_handler.notify_task_update.assert_awaited_once_with(
            message_id="msg-1",
            state=coerce_task_state("failed"),
            room_id="room-1",
            user_id="u1",
            error="Attached file report.pdf exceeds the inline A2A limit.",
        )

    @pytest.mark.asyncio
    async def test_process_queue_generic_failed_result_does_not_create_preflight_task(self):
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
        assert (
            qe.message_reader.get_room_agent_message_by_message_id.await_args_list[0]
            == call("sys-umsg-1")
        )
        qe.message_writer.add_room_agent_message.assert_awaited_once_with(sys_msg)
        qe.delivery.send_task_submitted.assert_awaited_once()
        assert (
            qe.delivery.send_task_submitted.await_args.kwargs["message_id"]
            == "sys-umsg-1"
        )

    @pytest.mark.asyncio
    async def test_process_queue_updates_system_hybro_task_through_focused_ports(self):
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
        qe.delivery.send_task_update.assert_awaited_once_with(
            room_id="room-1",
            message_id="sys-umsg-1",
            status="completed",
        )
        assert qe.message_reader.get_room_agent_message_by_message_id.await_args_list == [
            call("sys-umsg-1"),
            call("sys-umsg-1"),
        ]
        update_content = (
            qe.message_writer
            .update_room_agent_message_with_new_message_content_by_message_id
        )
        update_content.assert_awaited_once_with(
            "sys-umsg-1",
            system_content,
        )

    @pytest.mark.asyncio
    async def test_process_queue_cancels_on_cancellation_token(self):
        """Pre-cancelled token -> QueueResult.CANCELED on the first iteration."""
        qe = _make_queue_executor()
        order: list[str] = []

        msg = MagicMock(
            message_id="msg-1", step_number=1, total_steps=1, extend_info=None,
        )

        queue = deque([msg])

        token = CancellationToken(message_id="umsg-1")
        token.cancel()

        qe.tsm.transition_task = AsyncMock()
        qe.delivery.clear_cancellation = MagicMock()
        qe.message_writer.cancel_descendants = AsyncMock()
        emit = AsyncMock(side_effect=lambda *a, **k: order.append("emit"))
        qe.bind_execution_event_deps(emit)

        result = await qe.process_queue(queue, "room-1", "umsg-1", token=token)

        assert result.result == QueueResult.CANCELED
        qe.tsm.transition_task.assert_called_once_with(
            msg, TaskState.canceled, persist=True
        )
        emit.assert_awaited_once()
        qe.delivery.send_processing_status.assert_not_called()
        assert order == ["emit"]
        qe.delivery.clear_cancellation.assert_called_once_with("umsg-1")

    @pytest.mark.asyncio
    async def test_process_queue_records_before_awaiting_input_send(self):
        """HITL AWAITING_INPUT records before the frontend pause status."""
        qe = _make_queue_executor()
        order: list[str] = []

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
                status_message="Need more input",
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
        emit.assert_awaited_once()
        qe.delivery.send_processing_status.assert_not_called()
        assert order == ["emit"]

    @pytest.mark.asyncio
    async def test_deferred_sse_status_has_no_required_post_emit_business_side_effects(self):
        """Deferred terminal delivery leaves only cancellation-token cleanup after emit."""
        qe = _make_queue_executor()
        order: list[str] = []

        msg = MagicMock(
            message_id="msg-1", step_number=1, total_steps=1, extend_info=None,
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
        qe.delivery.clear_cancellation = MagicMock(
            side_effect=lambda *a, **k: order.append("clear-token")
        )
        emit = AsyncMock(side_effect=lambda *a, **k: order.append("emit"))
        qe.bind_execution_event_deps(emit)

        result = await qe.process_queue(queue, "room-1", "umsg-1", token=token)

        assert result.result == QueueResult.CANCELED
        assert order == [
            "cancel-task",
            "cancel-descendants",
            "emit",
            "clear-token",
        ]

    @pytest.mark.asyncio
    async def test_resume_from_continuation_failure_records_before_terminal_emit(self):
        """V1 resume failure runs caller-owned notification before terminal emit."""
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
        qe.delivery.get_token = MagicMock(return_value=None)
        qe.delivery.create_token = MagicMock(
            return_value=CancellationToken(message_id="umsg-1")
        )
        qe.process_queue = AsyncMock(
            return_value=QueueProcessingResult(result=QueueResult.FAILED)
        )
        emit = AsyncMock(side_effect=lambda *a, **k: order.append("emit"))
        qe.bind_execution_event_deps(emit)
        notify = AsyncMock(side_effect=lambda *a, **k: order.append("notify"))

        result = await qe.resume_from_continuation(
            "paused-msg",
            before_terminal_failure=notify,
        )

        assert result.success is False
        assert order == ["notify", "emit"]

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
                "remaining_queue": [
                    {"message_id": "msg-2", "room_id": "room-1"}
                ],
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
        qe.delivery.get_token = MagicMock(return_value=None)
        qe.delivery.create_token = MagicMock(
            return_value=CancellationToken(message_id="umsg-1")
        )
        qe.process_queue = AsyncMock(
            return_value=QueueProcessingResult(result=QueueResult.COMPLETED)
        )

        with patch("execution.orchestration.queue_executor.RoomAgentMessage") as MockRAM:
            MockRAM.model_validate.return_value = MagicMock(message_id="msg-2")
            result = await qe.resume_from_continuation(
                "paused-msg", task_result_text="task done"
            )

        assert result.success is True
        assert result.needs_completion is True
        assert result.room_id == "room-1"
        assert result.user_message_id == "umsg-1"
        qe.process_queue.assert_called_once()
        assert len(qe.event_publisher.internal_events) == 1
        event = qe.event_publisher.internal_events[0]
        assert isinstance(event, MessageCommitted)
        assert event.room_id == "room-1"
        assert event.message_id == "paused-msg"
        assert event.message_type == "agent"
        assert event.agent_id == "a1"
        assert event.agent_name == "TestAgent"
        assert event.was_successful is True


@pytest.mark.asyncio
async def test_deferred_sse_status_has_no_required_post_emit_business_side_effects():
    """Deferred terminal delivery leaves only cancellation-token cleanup after emit."""
    qe = _make_queue_executor()
    order: list[str] = []

    msg = MagicMock(
        message_id="msg-1", step_number=1, total_steps=1, extend_info=None,
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
    qe.delivery.clear_cancellation = MagicMock(
        side_effect=lambda *a, **k: order.append("clear-token")
    )
    emit = AsyncMock(side_effect=lambda *a, **k: order.append("emit"))
    qe.bind_execution_event_deps(emit)

    result = await qe.process_queue(queue, "room-1", "umsg-1", token=token)

    assert result.result == QueueResult.CANCELED
    assert order == [
        "cancel-task",
        "cancel-descendants",
        "emit",
        "clear-token",
    ]


@pytest.mark.asyncio
async def test_resume_from_continuation_failure_records_before_terminal_emit():
    """V1 resume failure runs caller-owned notification before terminal emit."""
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
    qe.delivery.get_token = MagicMock(return_value=None)
    qe.delivery.create_token = MagicMock(
        return_value=CancellationToken(message_id="umsg-1")
    )
    qe.process_queue = AsyncMock(
        return_value=QueueProcessingResult(result=QueueResult.FAILED)
    )
    emit = AsyncMock(side_effect=lambda *a, **k: order.append("emit"))
    qe.bind_execution_event_deps(emit)
    notify = AsyncMock(side_effect=lambda *a, **k: order.append("notify"))

    result = await qe.resume_from_continuation(
        "paused-msg",
        before_terminal_failure=notify,
    )

    assert result.success is False
    assert order == ["notify", "emit"]
