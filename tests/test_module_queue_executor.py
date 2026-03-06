"""
Unit tests for QueueExecutor module.

Tests cover:
- _check_rate_limit: allowed vs rate-limited
- QueueResult enum values
- _managed_queue cleanup behavior (RAII)
"""

import pytest
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

from a2a.types import TaskState

from common.utils.cancellation import CancellationToken
from models.processing import ProcessingResult, ProcessingStatus
from modules.QueueExecutor import QueueExecutor, QueueProcessingResult, QueueResult


# =============================================================================
# QueueResult Tests
# =============================================================================


class TestQueueResult:
    def test_enum_values(self):
        assert QueueResult.COMPLETED == "completed"
        assert QueueResult.CANCELED == "canceled"
        assert QueueResult.PAUSED == "paused"


# =============================================================================
# _check_rate_limit Tests
# =============================================================================


def _make_queue_executor():
    qe = object.__new__(QueueExecutor)
    qe.rate_limit_service = MagicMock()
    qe.sse_manager = MagicMock()
    qe.tsm = MagicMock()
    qe.database_service = MagicMock()
    qe.a2a_service = MagicMock()
    qe.room_services = MagicMock()
    qe.response_processor = MagicMock()
    qe.dispatcher = MagicMock()
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
        qe.sse_manager.send_rate_limit_error = AsyncMock()
        qe.tsm.transition_task = AsyncMock()

        agent = MagicMock()
        agent.agent_id = "a1"
        agent.rate_limit_per_user_per_hour = 100
        agent.rate_limit_system_per_hour = 1000

        msg = MagicMock()
        is_limited = await qe._check_rate_limit(msg, agent, "room-1", "umsg-1", "u1")

        assert is_limited is True
        qe.sse_manager.send_rate_limit_error.assert_called_once()
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
    async def test_process_single_message_dispatches_to_response_processor(self):
        """Inline path (no AgentMessageProcessor) routes through
        response_processor.handle_streaming_response."""
        qe = _make_queue_executor()
        qe._agent_message_processor = None

        msg = MagicMock()
        msg.message_id = "msg-1"
        msg.user_id = "u1"

        agent = MagicMock()
        agent.agent_card = MagicMock()

        qe.database_service.get_room_memory_by_room_id = AsyncMock(
            return_value=MagicMock()
        )

        process_resp = MagicMock()
        process_resp.success = True
        process_resp.a2a_message = MagicMock()
        qe.room_services.process_agent_message = AsyncMock(
            return_value=process_resp
        )

        qe.a2a_service.has_streaming_capability = MagicMock(return_value=True)
        qe.response_processor.handle_streaming_response = AsyncMock(
            return_value=(ProcessingStatus.SUCCESS, "reply")
        )
        qe.database_service.get_room_agent_message_by_message_id = AsyncMock(
            return_value=msg
        )

        with patch("models.request.RoomCenterAgentMessageRequest"):
            result = await qe._process_single_message(
                msg, "room-1", agent, "umsg-1"
            )

        qe.response_processor.handle_streaming_response.assert_called_once()
        assert result.status == ProcessingStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_process_queue_completes_all_messages(self):
        """Two-item queue where both succeed -> QueueResult.COMPLETED."""
        qe = _make_queue_executor()
        qe._agent_message_processor = None
        qe.room_memory_service = AsyncMock()

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
        qe.database_service.cancel_descendants = AsyncMock()

        result = await qe.process_queue(queue, "room-1", "umsg-1")

        assert result.result == QueueResult.COMPLETED
        assert qe._process_single_message.call_count == 2

    @pytest.mark.asyncio
    async def test_process_queue_cancels_on_cancellation_token(self):
        """Pre-cancelled token -> QueueResult.CANCELED on the first iteration."""
        qe = _make_queue_executor()

        msg = MagicMock(
            message_id="msg-1", step_number=1, total_steps=1, extend_info=None,
        )

        queue = deque([msg])

        token = CancellationToken(message_id="umsg-1")
        token.cancel()

        qe.tsm.transition_task = AsyncMock()
        qe.sse_manager.send_processing_status = AsyncMock()
        qe.sse_manager.clear_cancellation = MagicMock()
        qe.database_service.cancel_descendants = AsyncMock()

        result = await qe.process_queue(queue, "room-1", "umsg-1", token=token)

        assert result.result == QueueResult.CANCELED
        qe.tsm.transition_task.assert_called_once_with(
            msg, TaskState.canceled, persist=True
        )
        qe.sse_manager.send_processing_status.assert_called_once()
        qe.sse_manager.clear_cancellation.assert_called_once_with("umsg-1")

    @pytest.mark.asyncio
    async def test_save_continuation_persists_to_db(self):
        """_save_continuation serializes the queue and writes via database_service."""
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

        qe.database_service.save_continuation_on_message = AsyncMock(
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

        qe.database_service.save_continuation_on_message.assert_called_once_with(
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
        qe.room_memory_service = AsyncMock()

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

        qe.database_service.get_and_clear_continuation_on_message = AsyncMock(
            return_value=continuation
        )
        qe.sse_manager.get_token = MagicMock(return_value=None)
        qe.sse_manager.create_token = MagicMock(
            return_value=CancellationToken(message_id="umsg-1")
        )
        qe.process_queue = AsyncMock(
            return_value=QueueProcessingResult(result=QueueResult.COMPLETED)
        )

        with patch("modules.QueueExecutor.RoomAgentMessage") as MockRAM:
            MockRAM.model_validate.return_value = MagicMock(message_id="msg-2")
            result = await qe.resume_from_continuation(
                "paused-msg", task_result_text="task done"
            )

        assert result.success is True
        assert result.needs_completion is True
        assert result.room_id == "room-1"
        assert result.user_message_id == "umsg-1"
        qe.process_queue.assert_called_once()
        qe.room_memory_service.add_agent_response_to_memory.assert_called_once_with(
            room_id="room-1",
            agent_id="a1",
            agent_name="TestAgent",
            response_text="task done",
            was_successful=True,
        )
