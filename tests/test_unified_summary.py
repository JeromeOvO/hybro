"""Tests for RoomMessageCenter._emit_unified_summary."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.room import CoordinatorAgentId


@pytest.fixture
def rmc():
    """Build a RoomMessageCenter with mocked dependencies."""
    from modules.RoomMessageCenter import RoomMessageCenter

    center = RoomMessageCenter.__new__(RoomMessageCenter)
    center.sse_manager = AsyncMock()
    center.database_service = AsyncMock()
    center.room_coordinator_service = AsyncMock()
    center.room_services = AsyncMock()
    center.openai_service = AsyncMock()
    return center


class TestEmitUnifiedSummary:
    """Tests for _emit_unified_summary."""

    @pytest.mark.asyncio
    async def test_supervisor_synthesis_used_directly(self, rmc):
        """When synthesis_text is provided, it's used as-is without calling OpenAI."""
        rmc.database_service.upsert_room_agent_message = AsyncMock(return_value=True)
        rmc.database_service.get_room_user_message_by_message_id = AsyncMock(return_value=None)

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            synthesis_text="Supervisor generated this.",
        )

        # OpenAI should NOT be called
        rmc.openai_service.summarize_agent_responses.assert_not_awaited()
        # DB upsert should be called with deterministic message_id
        rmc.database_service.upsert_room_agent_message.assert_awaited_once()
        saved_msg = rmc.database_service.upsert_room_agent_message.call_args[0][0]
        assert saved_msg.message_id == "summary-msg-1"
        assert saved_msg.agent_id == CoordinatorAgentId.SUMMARY
        assert saved_msg.extend_info["summary_origin"] == "supervisor"
        # SSE agent_response should be sent
        rmc.sse_manager.send_agent_response.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_openai_fallback_with_trajectory(self, rmc):
        """When no synthesis_text, uses OpenAI with trajectory_responses."""
        rmc.database_service.upsert_room_agent_message = AsyncMock(return_value=True)
        rmc.database_service.get_room_user_message_by_message_id = AsyncMock(return_value=None)
        rmc.openai_service.summarize_agent_responses = AsyncMock(
            return_value="OpenAI summary."
        )

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "A", "message": "text A"},
                {"agent_name": "B", "message": "text B"},
            ],
            is_debate=True,
        )

        rmc.openai_service.summarize_agent_responses.assert_awaited_once_with(
            [
                {"agent_name": "A", "message": "text A"},
                {"agent_name": "B", "message": "text B"},
            ],
            mode="debate",
        )
        saved_msg = rmc.database_service.upsert_room_agent_message.call_args[0][0]
        assert saved_msg.extend_info["summary_origin"] == "coordinator"
        assert saved_msg.extend_info["summary_type"] == "debate"

    @pytest.mark.asyncio
    async def test_fewer_than_2_responses_skips(self, rmc):
        """When trajectory has < 2 responses, no summary emitted."""
        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "A", "message": "only one"},
            ],
        )

        rmc.database_service.upsert_room_agent_message.assert_not_awaited()
        # Placeholder should be dismissed
        rmc.sse_manager.send_task_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deterministic_message_id(self, rmc):
        """message_id is always summary-{user_message_id}."""
        rmc.database_service.upsert_room_agent_message = AsyncMock(return_value=True)
        rmc.database_service.get_room_user_message_by_message_id = AsyncMock(return_value=None)

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-abc-123",
            synthesis_text="test",
        )

        rmc.sse_manager.send_task_submitted.assert_awaited_once()
        call_kwargs = rmc.sse_manager.send_task_submitted.call_args[1]
        assert call_kwargs["message_id"] == "summary-msg-abc-123"

    @pytest.mark.asyncio
    async def test_failure_cleans_up_placeholder(self, rmc):
        """On exception, task_update(status=failed) is sent to dismiss spinner."""
        rmc.database_service.upsert_room_agent_message = AsyncMock(
            side_effect=Exception("DB down")
        )
        rmc.database_service.get_room_user_message_by_message_id = AsyncMock(return_value=None)

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            synthesis_text="will fail on save",
        )

        # Should attempt cleanup
        rmc.sse_manager.send_task_update.assert_awaited()
        cleanup_kwargs = rmc.sse_manager.send_task_update.call_args[1]
        assert cleanup_kwargs["status"] == "failed"
