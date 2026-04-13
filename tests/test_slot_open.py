import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_slot_lifecycle():
    lifecycle = MagicMock()
    lifecycle.open_slot = AsyncMock()
    lifecycle.terminate_slot = AsyncMock()
    return lifecycle


def _make_queue_executor(mock_slot_lifecycle):
    """Build a QueueExecutor with mocked dependencies."""
    from modules.QueueExecutor import QueueExecutor

    executor = QueueExecutor.__new__(QueueExecutor)
    executor._slot_lifecycle = mock_slot_lifecycle
    executor._turn_event_appender = MagicMock(append=AsyncMock())
    executor.tsm = MagicMock(transition_task=AsyncMock())
    executor.sse_manager = MagicMock(
        send_processing_status=AsyncMock(),
        send_task_submitted=AsyncMock(),
    )
    executor.agent_dispatcher = MagicMock()
    executor._agent_message_processor = MagicMock()
    executor.response_handler = MagicMock(
        notify_task_update=AsyncMock(return_value=True),
    )
    executor.database_service = MagicMock(
        cancel_descendants=AsyncMock(),
        get_room_agent_messages_by_related_id=AsyncMock(return_value=[]),
    )
    executor.rate_limit_service = MagicMock(
        record_request=AsyncMock(),
    )
    executor.room_services = MagicMock()
    executor.room_memory_service = MagicMock(
        add_agent_response_to_memory=AsyncMock(),
    )
    executor.debate_service = MagicMock()
    return executor


class TestSlotOpenedInQueueExecutor:
    """Verify QueueExecutor emits slot_opened after successful agent resolution."""

    @pytest.mark.asyncio
    async def test_slot_opened_emitted_after_agent_resolved(self, mock_slot_lifecycle):
        """When process_queue resolves an agent, slot_opened must be called."""
        from collections import deque
        from models.processing import ProcessingResult, ProcessingStatus

        executor = _make_queue_executor(mock_slot_lifecycle)

        msg = MagicMock()
        msg.message_id = "msg_123"
        msg.turn_id = "turn_1"
        msg.agent_id = "agent_abc"
        msg.step_number = 1
        msg.total_steps = 1
        msg.extend_info = None

        mock_agent = MagicMock()
        mock_agent.agent_id = "agent_abc"
        mock_agent.agent_card = MagicMock(name="Test Agent")
        executor._resolve_agent_for_message = AsyncMock(return_value=mock_agent)
        executor._process_single_message = AsyncMock(
            return_value=ProcessingResult(status=ProcessingStatus.SUCCESS)
        )
        executor._queue_next_messages = AsyncMock()

        queue = deque([msg])
        await executor.process_queue(queue, room_id="room_1", user_message_id="turn_1")

        mock_slot_lifecycle.open_slot.assert_called_once()
        call_kw = mock_slot_lifecycle.open_slot.call_args.kwargs
        assert call_kw["room_id"] == "room_1"
        assert call_kw["turn_id"] == "turn_1"
        assert call_kw["slot_id"] == "msg_123"
        assert call_kw["slot_type"] == "agent"

    @pytest.mark.asyncio
    async def test_slot_opened_skipped_when_no_turn_id(self, mock_slot_lifecycle):
        """Old messages without turn_id should not trigger slot_opened."""
        from collections import deque
        from models.processing import ProcessingResult, ProcessingStatus

        executor = _make_queue_executor(mock_slot_lifecycle)

        msg = MagicMock()
        msg.message_id = "msg_old"
        msg.turn_id = None  # pre-Phase-0 message
        msg.agent_id = "agent_abc"
        msg.step_number = 1
        msg.total_steps = 1
        msg.extend_info = None

        mock_agent = MagicMock()
        mock_agent.agent_id = "agent_abc"
        mock_agent.agent_card = MagicMock(name="Test Agent")
        executor._resolve_agent_for_message = AsyncMock(return_value=mock_agent)
        executor._process_single_message = AsyncMock(
            return_value=ProcessingResult(status=ProcessingStatus.SUCCESS)
        )
        executor._queue_next_messages = AsyncMock()

        queue = deque([msg])
        await executor.process_queue(queue, room_id="room_1", user_message_id="user_old")

        mock_slot_lifecycle.open_slot.assert_not_called()
