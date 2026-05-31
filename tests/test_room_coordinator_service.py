"""Unit tests for RoomCoordinatorService.on_room_user_message_completed.

.. deprecated::
    ``on_room_user_message_completed`` is deprecated in favour of
    ``RoomMessageCenter._emit_unified_summary()``.  These tests are
    retained for backward-compatibility coverage of the legacy method.
    New summary behaviour is tested in ``tests/test_unified_summary.py``.

Covers the ``trajectory_responses`` fast-path that was added to avoid a
race condition where relay agents' DB messages are not yet written when the
coordinator tries to read them.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from models.room import Room

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_room(debate_mode: bool = False) -> Room:
    room = MagicMock(spec=Room)
    room.extend_info = {"debateMode": debate_mode} if debate_mode else {}
    return room


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def coordinator():
    """Build a RoomCoordinatorService with all external dependencies mocked."""
    from services.room_coordinator_service import RoomCoordinatorService

    svc = RoomCoordinatorService.__new__(RoomCoordinatorService)
    svc.database_service = AsyncMock()
    svc.openai_service = AsyncMock()
    svc.sse_manager = AsyncMock()

    # Stub _create_and_emit_summary_message so we can assert without
    # needing the full SSE/DB chain.
    svc._create_and_emit_summary_message = AsyncMock()

    yield svc


# ---------------------------------------------------------------------------
# Tests: trajectory_responses fast-path
# ---------------------------------------------------------------------------


class TestOnRoomUserMessageCompletedTrajectoryPath:
    """Verify behaviour when trajectory_responses is provided."""

    @pytest.mark.asyncio
    async def test_two_responses_debate_mode_generates_summary(self, coordinator):
        """Two trajectory entries in debate mode → debate summary generated."""
        coordinator.database_service.get_room_by_room_id = AsyncMock(
            return_value=_make_room(debate_mode=True)
        )
        coordinator.openai_service.summarize_agent_responses = AsyncMock(
            return_value="Debate summary text."
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "Agent Alpha", "message": "Alpha says yes."},
                {"agent_name": "Agent Beta", "message": "Beta says no."},
            ],
        )

        coordinator.openai_service.summarize_agent_responses.assert_awaited_once_with(
            [
                {"agent_name": "Agent Alpha", "message": "Alpha says yes."},
                {"agent_name": "Agent Beta", "message": "Beta says no."},
            ],
            mode="debate",
            user_question=None,
        )
        coordinator._create_and_emit_summary_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_two_responses_non_debate_mode_generates_summary(self, coordinator):
        """Two trajectory entries in non-debate mode → non_debate summary."""
        coordinator.database_service.get_room_by_room_id = AsyncMock(
            return_value=_make_room(debate_mode=False)
        )
        coordinator.openai_service.summarize_agent_responses = AsyncMock(
            return_value="Combined summary text."
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "Agent A", "message": "Response from A."},
                {"agent_name": "Agent B", "message": "Response from B."},
            ],
        )

        coordinator.openai_service.summarize_agent_responses.assert_awaited_once_with(
            [
                {"agent_name": "Agent A", "message": "Response from A."},
                {"agent_name": "Agent B", "message": "Response from B."},
            ],
            mode="non_debate",
            user_question=None,
        )
        coordinator._create_and_emit_summary_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_one_response_skips_summary(self, coordinator):
        """Only one trajectory entry → summary requires ≥2, so nothing emitted."""
        coordinator.database_service.get_room_by_room_id = AsyncMock(
            return_value=_make_room(debate_mode=True)
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "Lone Agent", "message": "Only one answer."},
            ],
        )

        coordinator.openai_service.summarize_agent_responses.assert_not_awaited()
        coordinator._create_and_emit_summary_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_bfs_not_called_when_trajectory_provided(self, coordinator):
        """When trajectory_responses is supplied, BFS DB read must be skipped."""
        coordinator.database_service.get_room_by_room_id = AsyncMock(
            return_value=_make_room(debate_mode=True)
        )
        coordinator.database_service.get_room_agent_messages_by_related_message_id = (
            AsyncMock()
        )
        coordinator.openai_service.summarize_agent_responses = AsyncMock(
            return_value="summary"
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "A", "message": "text A"},
                {"agent_name": "B", "message": "text B"},
            ],
        )

        coordinator.database_service.get_room_agent_messages_by_related_message_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_trajectory_responses_falls_back_to_db(self, coordinator):
        """Empty list is falsy → falls back to DB BFS path."""
        coordinator.database_service.get_room_by_room_id = AsyncMock(
            return_value=_make_room(debate_mode=True)
        )
        # BFS returns nothing so summary is skipped — we just verify the BFS was called.
        coordinator.database_service.get_room_agent_messages_by_related_message_id = (
            AsyncMock(return_value=[])
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[],
        )

        coordinator.database_service.get_room_agent_messages_by_related_message_id.assert_awaited_once_with(
            "msg-1"
        )
        coordinator._create_and_emit_summary_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_trajectory_responses_falls_back_to_db(self, coordinator):
        """None → falls back to DB BFS path (existing behaviour)."""
        coordinator.database_service.get_room_by_room_id = AsyncMock(
            return_value=_make_room(debate_mode=True)
        )
        coordinator.database_service.get_room_agent_messages_by_related_message_id = (
            AsyncMock(return_value=[])
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=None,
        )

        coordinator.database_service.get_room_agent_messages_by_related_message_id.assert_awaited_once_with(
            "msg-1"
        )

    @pytest.mark.asyncio
    async def test_room_not_found_returns_early(self, coordinator):
        """If the room doesn't exist, nothing is attempted."""
        coordinator.database_service.get_room_by_room_id = AsyncMock(return_value=None)

        await coordinator.on_room_user_message_completed(
            room_id="missing-room",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "A", "message": "text"},
                {"agent_name": "B", "message": "text"},
            ],
        )

        coordinator.openai_service.summarize_agent_responses.assert_not_awaited()
        coordinator._create_and_emit_summary_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_summarize_returns_empty_skips_emit(self, coordinator):
        """If the LLM returns an empty summary, no message is emitted."""
        coordinator.database_service.get_room_by_room_id = AsyncMock(
            return_value=_make_room(debate_mode=True)
        )
        coordinator.openai_service.summarize_agent_responses = AsyncMock(
            return_value=""
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "A", "message": "response A"},
                {"agent_name": "B", "message": "response B"},
            ],
        )

        coordinator._create_and_emit_summary_message.assert_not_awaited()
