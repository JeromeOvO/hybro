"""Unit tests for SynthesisCoordinator.on_room_user_message_completed.

.. deprecated::
    ``on_room_user_message_completed`` is deprecated in favour of
    ``RoomMessageCenter._emit_unified_summary()``.  These tests are
    retained for backward-compatibility coverage of the legacy method.
    New summary behaviour is tested in ``tests/test_unified_summary.py``.

Covers the ``trajectory_responses`` fast-path that was added to avoid a
race condition where relay agents' DB messages are not yet written when the
coordinator tries to read them.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.dto import RoomMessageSummary
from execution.orchestration.synthesis_coordinator import SynthesisCoordinator
from llm_gateway.errors import LLMServiceNotBoundError
from models.room import Room

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_room() -> Room:
    room = MagicMock(spec=Room)
    room.extend_info = {}
    return room


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def coordinator():
    """Build a SynthesisCoordinator with all external dependencies mocked."""
    store = AsyncMock()
    store.get_room_by_room_id = AsyncMock(return_value=None)
    store.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    store.get_room_agent_messages_by_related_message_id = AsyncMock(return_value=[])
    store.get_agent_name_by_agent_id = AsyncMock(return_value=None)
    store.add_room_agent_message = AsyncMock()
    svc = SynthesisCoordinator(
        message_store=store,
        delivery=AsyncMock(),
    )
    svc.summary_service = MagicMock()
    svc.summary_service.summarize_agent_responses_stream = MagicMock(
        return_value=_stream_text("Summary text.")
    )

    # Stub _create_and_emit_summary_message so we can assert without
    # needing the full SSE/DB chain.
    svc._create_and_emit_summary_message = AsyncMock()

    yield svc


async def _stream_text(text: str):
    yield text


# ---------------------------------------------------------------------------
# Tests: trajectory_responses fast-path
# ---------------------------------------------------------------------------


class TestOnRoomUserMessageCompletedTrajectoryPath:
    """Verify behaviour when trajectory_responses is provided."""

    @pytest.mark.asyncio
    async def test_two_responses_generate_summary(self, coordinator):
        """Two trajectory entries produce one summary."""
        coordinator._message_store.get_room_by_room_id = AsyncMock(
            return_value=_make_room()
        )
        coordinator.summary_service.summarize_agent_responses_stream = MagicMock(
            return_value=_stream_text("Debate summary text.")
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "Agent Alpha", "message": "Alpha says yes."},
                {"agent_name": "Agent Beta", "message": "Beta says no."},
            ],
        )

        coordinator.summary_service.summarize_agent_responses_stream.assert_called_once()
        passed_responses = (
            coordinator.summary_service.summarize_agent_responses_stream.call_args[0][0]
        )
        assert [item.agent_name for item in passed_responses] == [
            "Agent Alpha",
            "Agent Beta",
        ]
        coordinator.summary_service.summarize_agent_responses_stream.assert_called_with(
            passed_responses,
            user_question=None,
        )
        coordinator._create_and_emit_summary_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_bound_summary_service_when_available(self, coordinator):
        coordinator._message_store.get_room_by_room_id = AsyncMock(
            return_value=_make_room()
        )
        coordinator.summary_service = MagicMock()
        coordinator.summary_service.summarize_agent_responses_stream = MagicMock(
            return_value=_stream_text("Focused summary text.")
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "Agent A", "message": "Response from A."},
                {"agent_name": "Agent B", "message": "Response from B."},
            ],
        )

        coordinator.summary_service.summarize_agent_responses_stream.assert_called_once()
        passed_responses = (
            coordinator.summary_service.summarize_agent_responses_stream.call_args[0][0]
        )
        assert all(isinstance(item, RoomMessageSummary) for item in passed_responses)
        assert passed_responses[0].agent_name == "Agent A"
        coordinator._create_and_emit_summary_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_summary_service_fails_fast(self, coordinator):
        coordinator._message_store.get_room_by_room_id = AsyncMock(
            return_value=_make_room()
        )
        coordinator.summary_service = None

        with pytest.raises(LLMServiceNotBoundError):
            await coordinator.on_room_user_message_completed(
                room_id="room-1",
                room_user_message_id="msg-1",
                trajectory_responses=[
                    {"agent_name": "Agent A", "message": "Response from A."},
                    {"agent_name": "Agent B", "message": "Response from B."},
                ],
            )

    @pytest.mark.asyncio
    async def test_two_responses_use_default_summary_prompt(self, coordinator):
        """Two trajectory entries use the unified summary prompt."""
        coordinator._message_store.get_room_by_room_id = AsyncMock(
            return_value=_make_room()
        )
        coordinator.summary_service.summarize_agent_responses_stream = MagicMock(
            return_value=_stream_text("Combined summary text.")
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "Agent A", "message": "Response from A."},
                {"agent_name": "Agent B", "message": "Response from B."},
            ],
        )

        coordinator.summary_service.summarize_agent_responses_stream.assert_called_once()
        passed_responses = (
            coordinator.summary_service.summarize_agent_responses_stream.call_args[0][0]
        )
        assert [item.agent_name for item in passed_responses] == ["Agent A", "Agent B"]
        coordinator.summary_service.summarize_agent_responses_stream.assert_called_with(
            passed_responses,
            user_question=None,
        )
        coordinator._create_and_emit_summary_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_one_response_skips_summary(self, coordinator):
        """Only one trajectory entry → summary requires ≥2, so nothing emitted."""
        coordinator._message_store.get_room_by_room_id = AsyncMock(
            return_value=_make_room()
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "Lone Agent", "message": "Only one answer."},
            ],
        )

        coordinator.summary_service.summarize_agent_responses_stream.assert_not_called()
        coordinator._create_and_emit_summary_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_bfs_not_called_when_trajectory_provided(self, coordinator):
        """When trajectory_responses is supplied, BFS DB read must be skipped."""
        coordinator._message_store.get_room_by_room_id = AsyncMock(
            return_value=_make_room()
        )
        coordinator._message_store.get_room_agent_messages_by_related_message_id = (
            AsyncMock()
        )
        coordinator.summary_service.summarize_agent_responses_stream = MagicMock(
            return_value=_stream_text("summary")
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "A", "message": "text A"},
                {"agent_name": "B", "message": "text B"},
            ],
        )

        coordinator._message_store.get_room_agent_messages_by_related_message_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_trajectory_responses_falls_back_to_db(self, coordinator):
        """Empty list is falsy → falls back to DB BFS path."""
        coordinator._message_store.get_room_by_room_id = AsyncMock(
            return_value=_make_room()
        )
        # BFS returns nothing so summary is skipped — we just verify the BFS was called.
        coordinator._message_store.get_room_agent_messages_by_related_message_id = (
            AsyncMock(return_value=[])
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=[],
        )

        coordinator._message_store.get_room_agent_messages_by_related_message_id.assert_awaited_once_with(
            "msg-1"
        )
        coordinator._create_and_emit_summary_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_trajectory_responses_falls_back_to_db(self, coordinator):
        """None → falls back to DB BFS path (existing behaviour)."""
        coordinator._message_store.get_room_by_room_id = AsyncMock(
            return_value=_make_room()
        )
        coordinator._message_store.get_room_agent_messages_by_related_message_id = (
            AsyncMock(return_value=[])
        )

        await coordinator.on_room_user_message_completed(
            room_id="room-1",
            room_user_message_id="msg-1",
            trajectory_responses=None,
        )

        coordinator._message_store.get_room_agent_messages_by_related_message_id.assert_awaited_once_with(
            "msg-1"
        )

    @pytest.mark.asyncio
    async def test_room_not_found_returns_early(self, coordinator):
        """If the room doesn't exist, nothing is attempted."""
        coordinator._message_store.get_room_by_room_id = AsyncMock(return_value=None)

        await coordinator.on_room_user_message_completed(
            room_id="missing-room",
            room_user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "A", "message": "text"},
                {"agent_name": "B", "message": "text"},
            ],
        )

        coordinator.summary_service.summarize_agent_responses_stream.assert_not_called()
        coordinator._create_and_emit_summary_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_summarize_returns_empty_skips_emit(self, coordinator):
        """If the LLM returns an empty summary, no message is emitted."""
        coordinator._message_store.get_room_by_room_id = AsyncMock(
            return_value=_make_room()
        )
        coordinator.summary_service.summarize_agent_responses_stream = MagicMock(
            return_value=_stream_text("")
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


def test_synthesis_coordinator_default_constructor_does_not_import_database_service():
    with patch(
        "importlib.import_module",
        side_effect=AssertionError("legacy import attempted"),
    ):
        svc = SynthesisCoordinator()

    assert svc._message_store is not None
