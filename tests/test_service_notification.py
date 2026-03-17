"""
Unit tests for NotificationService (notification_service.py).

Tests cover:
- send_task_update: event formatting and SSE delegation
- Skips when message_id is missing
- Agent name resolution from agent_card fallback
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.notification_service import NotificationService


@pytest.fixture
def notif_svc():
    """Create NotificationService with mocked SSE manager."""
    svc = object.__new__(NotificationService)
    svc.sse_manager = MagicMock()
    svc.sse_manager.send_task_update = AsyncMock()
    return svc


# =============================================================================
# send_task_update Tests
# =============================================================================


class TestSendTaskUpdate:
    """Tests for task update notification formatting and dispatch."""

    @pytest.mark.asyncio
    async def test_sends_update_with_all_fields(self, notif_svc):
        await notif_svc.send_task_update(
            room_id="room-1",
            message_id="msg-1",
            status="completed",
            agent_name="TestAgent",
            agent_id="agent-1",
            content="Result content",
        )

        notif_svc.sse_manager.send_task_update.assert_called_once()
        kwargs = notif_svc.sse_manager.send_task_update.call_args.kwargs
        assert kwargs["room_id"] == "room-1"
        assert kwargs["message_id"] == "msg-1"
        assert kwargs["status"] == "completed"
        assert kwargs["agent_name"] == "TestAgent"
        assert kwargs["content"] == "Result content"

    @pytest.mark.asyncio
    async def test_skips_when_message_id_is_none(self, notif_svc):
        await notif_svc.send_task_update(
            room_id="room-1",
            message_id=None,
            status="working",
        )
        notif_svc.sse_manager.send_task_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_message_id_is_empty(self, notif_svc):
        await notif_svc.send_task_update(
            room_id="room-1",
            message_id="",
            status="working",
        )
        notif_svc.sse_manager.send_task_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolves_name_from_agent_card(self, notif_svc):
        """When agent_name is None but agent_card is provided, use card name."""
        mock_card = MagicMock()
        mock_card.name = "CardAgent"

        await notif_svc.send_task_update(
            room_id="room-1",
            message_id="msg-1",
            status="working",
            agent_card=mock_card,
        )

        kwargs = notif_svc.sse_manager.send_task_update.call_args.kwargs
        assert kwargs["agent_name"] == "CardAgent"

    @pytest.mark.asyncio
    async def test_explicit_name_overrides_card(self, notif_svc):
        """agent_name takes priority over agent_card.name."""
        mock_card = MagicMock()
        mock_card.name = "CardAgent"

        await notif_svc.send_task_update(
            room_id="room-1",
            message_id="msg-1",
            status="working",
            agent_name="ExplicitName",
            agent_card=mock_card,
        )

        kwargs = notif_svc.sse_manager.send_task_update.call_args.kwargs
        assert kwargs["agent_name"] == "ExplicitName"

    @pytest.mark.asyncio
    async def test_passes_optional_fields(self, notif_svc):
        await notif_svc.send_task_update(
            room_id="room-1",
            message_id="msg-1",
            status="working",
            step_number=2,
            total_steps=5,
            task_content="Summarize document",
            related_message_id="related-1",
        )

        kwargs = notif_svc.sse_manager.send_task_update.call_args.kwargs
        assert kwargs["step_number"] == 2
        assert kwargs["total_steps"] == 5
        assert kwargs["task_content"] == "Summarize document"
        assert kwargs["related_message_id"] == "related-1"
