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


def _make_agent_message(agent_id, text, *, completed=True, extend_info=None):
    """Build a minimal RoomAgentMessage-like object for testing."""
    from unittest.mock import MagicMock
    from a2a.types import TaskState

    msg = MagicMock()
    msg.agent_id = agent_id
    msg.extend_info = extend_info

    task = MagicMock()
    task.status.state = TaskState.completed if completed else TaskState.working
    msg.message_content.message_task = task

    # Make extract_agent_text_from_room_message return the text
    history_msg = MagicMock()
    history_msg.role.value = "agent"
    part = MagicMock()
    part.text = text
    part.model_fields_set = {"text"}
    history_msg.parts = [part]
    task.history = [history_msg]

    return msg


class TestEmitUnifiedSummary:
    """Tests for _emit_unified_summary."""

    @pytest.mark.asyncio
    async def test_supervisor_synthesis_used_directly(self, rmc):
        """When synthesis_text is provided with 2+ trajectory responses, it's used as-is."""
        rmc.database_service.upsert_room_agent_message = AsyncMock(return_value=True)
        rmc.database_service.get_room_user_message_by_message_id = AsyncMock(return_value=None)

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            synthesis_text="Supervisor generated this.",
            trajectory_responses=[
                {"agent_name": "A", "message": "text A"},
                {"agent_name": "B", "message": "text B"},
            ],
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
    async def test_supervisor_synthesis_single_agent_skipped(self, rmc):
        """When supervisor synthesis is provided but only 1 agent responded, skip summary.

        The individual agent's task_update SSE already delivers the content,
        so emitting a redundant synthesis summary would create a duplicate.
        """
        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            synthesis_text="Supervisor generated this.",
            trajectory_responses=[
                {"agent_name": "A", "message": "only one agent"},
            ],
        )

        # No SSE events should be emitted
        rmc.sse_manager.send_task_submitted.assert_not_awaited()
        rmc.sse_manager.send_agent_response.assert_not_awaited()
        # No DB write
        rmc.database_service.upsert_room_agent_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_supervisor_synthesis_zero_agents_skipped(self, rmc):
        """When supervisor synthesis is provided but 0 agents responded, skip summary."""
        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            synthesis_text="Supervisor generated this.",
            trajectory_responses=[],
        )

        rmc.sse_manager.send_task_submitted.assert_not_awaited()
        rmc.sse_manager.send_agent_response.assert_not_awaited()
        rmc.database_service.upsert_room_agent_message.assert_not_awaited()

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
            user_question=None,
        )
        saved_msg = rmc.database_service.upsert_room_agent_message.call_args[0][0]
        assert saved_msg.extend_info["summary_origin"] == "coordinator"
        assert saved_msg.extend_info["summary_type"] == "debate"

    @pytest.mark.asyncio
    async def test_fewer_than_2_trajectory_responses_skips_silently(self, rmc):
        """When trajectory has < 2 responses, no SSE events emitted at all."""
        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "A", "message": "only one"},
            ],
        )

        rmc.database_service.upsert_room_agent_message.assert_not_awaited()
        rmc.sse_manager.send_task_submitted.assert_not_awaited()
        rmc.sse_manager.send_task_update.assert_not_awaited()
        rmc.sse_manager.send_agent_response.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_trajectory_skips_silently(self, rmc):
        """When trajectory is empty, no SSE events emitted at all."""
        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            trajectory_responses=[],
        )

        rmc.database_service.upsert_room_agent_message.assert_not_awaited()
        rmc.sse_manager.send_task_submitted.assert_not_awaited()
        rmc.sse_manager.send_task_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_agent_from_db_skips_silently(self, rmc):
        """When DB returns only 1 agent message, no summary bubble appears."""
        single_msg = _make_agent_message("agent-1", "Hello from agent")
        rmc.room_coordinator_service._collect_agent_messages_for_user_message = (
            AsyncMock(return_value=[single_msg])
        )
        rmc.database_service.get_agent_name_by_agent_id = AsyncMock(
            return_value="Agent One"
        )

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
        )

        # No SSE events should be emitted
        rmc.sse_manager.send_task_submitted.assert_not_awaited()
        rmc.sse_manager.send_task_update.assert_not_awaited()
        rmc.sse_manager.send_agent_response.assert_not_awaited()
        # No DB write
        rmc.database_service.upsert_room_agent_message.assert_not_awaited()
        # OpenAI should NOT be called
        rmc.openai_service.summarize_agent_responses.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_zero_agents_from_db_skips_silently(self, rmc):
        """When DB returns no agent messages, no summary bubble appears."""
        rmc.room_coordinator_service._collect_agent_messages_for_user_message = (
            AsyncMock(return_value=[])
        )

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
        )

        rmc.sse_manager.send_task_submitted.assert_not_awaited()
        rmc.sse_manager.send_task_update.assert_not_awaited()
        rmc.sse_manager.send_agent_response.assert_not_awaited()
        rmc.database_service.upsert_room_agent_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_two_agents_from_db_emits_summary(self, rmc):
        """When DB returns 2+ agent messages, summary is generated and emitted."""
        msgs = [
            _make_agent_message("agent-1", "Response from A"),
            _make_agent_message("agent-2", "Response from B"),
        ]
        rmc.room_coordinator_service._collect_agent_messages_for_user_message = (
            AsyncMock(return_value=msgs)
        )
        rmc.database_service.get_agent_name_by_agent_id = AsyncMock(
            side_effect=["Agent A", "Agent B"]
        )
        rmc.openai_service.summarize_agent_responses = AsyncMock(
            return_value="Combined summary."
        )
        rmc.database_service.upsert_room_agent_message = AsyncMock(return_value=True)
        rmc.database_service.get_room_user_message_by_message_id = AsyncMock(
            return_value=None
        )

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
        )

        # OpenAI called with both responses
        rmc.openai_service.summarize_agent_responses.assert_awaited_once()
        # Placeholder SSE emitted
        rmc.sse_manager.send_task_submitted.assert_awaited_once()
        # Summary persisted
        rmc.database_service.upsert_room_agent_message.assert_awaited_once()
        # Final SSE emitted
        rmc.sse_manager.send_agent_response.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_openai_returns_empty_emits_failed(self, rmc):
        """When OpenAI returns empty content, the working card is dismissed with failed status."""
        rmc.openai_service.summarize_agent_responses = AsyncMock(return_value="")

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "A", "message": "text A"},
                {"agent_name": "B", "message": "text B"},
            ],
        )

        rmc.sse_manager.send_task_submitted.assert_awaited_once()
        rmc.sse_manager.send_task_update.assert_awaited_once()
        update_kwargs = rmc.sse_manager.send_task_update.call_args
        assert update_kwargs[0][2] == "failed"
        rmc.database_service.upsert_room_agent_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deterministic_message_id(self, rmc):
        """message_id is always summary-{user_message_id}."""
        rmc.database_service.upsert_room_agent_message = AsyncMock(return_value=True)
        rmc.database_service.get_room_user_message_by_message_id = AsyncMock(return_value=None)

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-abc-123",
            synthesis_text="test",
            trajectory_responses=[
                {"agent_name": "A", "message": "text A"},
                {"agent_name": "B", "message": "text B"},
            ],
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
            trajectory_responses=[
                {"agent_name": "A", "message": "text A"},
                {"agent_name": "B", "message": "text B"},
            ],
        )

        # Should attempt cleanup
        rmc.sse_manager.send_task_update.assert_awaited()
        cleanup_kwargs = rmc.sse_manager.send_task_update.call_args[1]
        assert cleanup_kwargs["status"] == "failed"
