"""
Unit tests for TaskUpdateNotifier (task_notifier.py).

Tests cover:
- send_task_update: event formatting and SSE delegation
- Skips when message_id is missing
- Agent name resolution from agent_card fallback
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from delivery.task_notifier import TaskUpdateNotifier


@pytest.fixture
def task_notifier():
    """Create TaskUpdateNotifier with mocked delivery facade."""
    delivery = MagicMock()
    delivery.send_task_update = AsyncMock()
    return TaskUpdateNotifier(delivery)


# =============================================================================
# send_task_update Tests
# =============================================================================


class TestSendTaskUpdate:
    """Tests for task update notification formatting and dispatch."""

    @pytest.mark.asyncio
    async def test_sends_update_with_all_fields(self, task_notifier):
        await task_notifier.send_task_update(
            room_id="room-1",
            message_id="msg-1",
            status="completed",
            agent_name="TestAgent",
            agent_id="agent-1",
            content="Result content",
        )

        task_notifier.delivery.send_task_update.assert_called_once()
        kwargs = task_notifier.delivery.send_task_update.call_args.kwargs
        assert kwargs["room_id"] == "room-1"
        assert kwargs["message_id"] == "msg-1"
        assert kwargs["status"] == "completed"
        assert kwargs["agent_name"] == "TestAgent"
        assert kwargs["content"] == "Result content"

    @pytest.mark.asyncio
    async def test_skips_when_message_id_is_none(self, task_notifier):
        await task_notifier.send_task_update(
            room_id="room-1",
            message_id=None,
            status="working",
        )
        task_notifier.delivery.send_task_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_message_id_is_empty(self, task_notifier):
        await task_notifier.send_task_update(
            room_id="room-1",
            message_id="",
            status="working",
        )
        task_notifier.delivery.send_task_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolves_name_from_agent_card(self, task_notifier):
        """When agent_name is None but agent_card is provided, use card name."""
        mock_card = MagicMock()
        mock_card.name = "CardAgent"

        await task_notifier.send_task_update(
            room_id="room-1",
            message_id="msg-1",
            status="working",
            agent_card=mock_card,
        )

        kwargs = task_notifier.delivery.send_task_update.call_args.kwargs
        assert kwargs["agent_name"] == "CardAgent"

    @pytest.mark.asyncio
    async def test_explicit_name_overrides_card(self, task_notifier):
        """agent_name takes priority over agent_card.name."""
        mock_card = MagicMock()
        mock_card.name = "CardAgent"

        await task_notifier.send_task_update(
            room_id="room-1",
            message_id="msg-1",
            status="working",
            agent_name="ExplicitName",
            agent_card=mock_card,
        )

        kwargs = task_notifier.delivery.send_task_update.call_args.kwargs
        assert kwargs["agent_name"] == "ExplicitName"

    @pytest.mark.asyncio
    async def test_passes_optional_fields(self, task_notifier):
        await task_notifier.send_task_update(
            room_id="room-1",
            message_id="msg-1",
            status="working",
            step_number=2,
            total_steps=5,
            task_content="Summarize document",
            related_message_id="related-1",
        )

        kwargs = task_notifier.delivery.send_task_update.call_args.kwargs
        assert kwargs["step_number"] == 2
        assert kwargs["total_steps"] == 5
        assert kwargs["task_content"] == "Summarize document"
        assert kwargs["related_message_id"] == "related-1"
