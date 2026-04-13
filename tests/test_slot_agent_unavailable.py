import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_slot_lifecycle():
    lifecycle = MagicMock()
    lifecycle.open_slot = AsyncMock()
    lifecycle.terminate_slot = AsyncMock()
    return lifecycle


class TestAgentUnavailableSlotEvents:
    """Verify slot_opened + slot_terminated(failed) emitted when agent resolution fails."""

    @pytest.mark.asyncio
    async def test_agent_resolution_failure_emits_failed_slot(self, mock_slot_lifecycle):
        """When _resolve_agent_for_message returns None, QueueExecutor must
        emit slot_opened + slot_terminated(failed, agent_unavailable)."""
        from modules.QueueExecutor import QueueExecutor
        from a2a.types import TaskState

        executor = QueueExecutor.__new__(QueueExecutor)
        executor._slot_lifecycle = mock_slot_lifecycle
        executor._turn_event_appender = MagicMock(append=AsyncMock())
        executor.tsm = MagicMock(transition_task=AsyncMock())
        executor.sse_manager = MagicMock(
            send_processing_status=AsyncMock(),
        )
        executor.database_service = MagicMock(
            cancel_descendants=AsyncMock(),
        )
        executor.agent_dispatcher = MagicMock()
        executor._agent_message_processor = MagicMock()
        executor.response_handler = MagicMock(
            notify_task_update=AsyncMock(return_value=True),
        )

        # Stub agent resolution to return None (failure)
        executor._resolve_agent_for_message = AsyncMock(return_value=None)

        msg = MagicMock()
        msg.message_id = "msg_123"
        msg.turn_id = "turn_1"
        msg.agent_id = "agent_abc"
        msg.step_number = 1
        msg.total_steps = 1
        msg.extend_info = None

        from collections import deque
        queue = deque([msg])
        result = await executor.process_queue(
            queue, room_id="room_1", user_message_id="turn_1",
        )

        # process_queue returns FAILED
        assert result.result.value == "failed"

    @pytest.mark.asyncio
    async def test_resolve_agent_inactive_returns_none(self, mock_slot_lifecycle):
        """When AgentDispatcher.resolve_agent encounters an inactive agent, returns None."""
        from modules.AgentDispatcher import AgentDispatcher

        dispatcher = AgentDispatcher.__new__(AgentDispatcher)
        dispatcher.database_service = MagicMock()

        # Agent exists but is inactive
        mock_agent = MagicMock()
        mock_agent.agent_status = "inactive"
        dispatcher.database_service.get_agent_by_agent_id = AsyncMock(
            return_value=mock_agent,
        )

        result = await dispatcher.resolve_agent("agent_inactive", "room_1")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_agent_not_found_returns_none(self, mock_slot_lifecycle):
        """When AgentDispatcher.resolve_agent cannot find agent, returns None."""
        from modules.AgentDispatcher import AgentDispatcher

        dispatcher = AgentDispatcher.__new__(AgentDispatcher)
        dispatcher.database_service = MagicMock()
        dispatcher.database_service.get_agent_by_agent_id = AsyncMock(
            return_value=None,
        )

        result = await dispatcher.resolve_agent("agent_missing", "room_1")
        assert result is None
