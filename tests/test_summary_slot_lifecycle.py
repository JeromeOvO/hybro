import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_slot_lifecycle():
    lifecycle = MagicMock()
    lifecycle.open_slot = AsyncMock()
    lifecycle.terminate_slot = AsyncMock()
    return lifecycle


class TestSummarySlotLifecycle:
    """Verify summary slot events in _emit_unified_summary."""

    @pytest.mark.asyncio
    async def test_summary_slot_opened_with_summary_type(self, mock_slot_lifecycle):
        """Summary slot should open with slot_type='summary'."""
        await mock_slot_lifecycle.open_slot(
            room_id="room_1",
            turn_id="turn_1",
            slot_id="summary-user_msg_1",
            slot_type="summary",
            mode="supervisor",
        )
        call_args = mock_slot_lifecycle.open_slot.call_args
        assert call_args.kwargs["slot_type"] == "summary"
        assert call_args.kwargs["mode"] == "supervisor"

    @pytest.mark.asyncio
    async def test_summary_slot_terminated_with_content(self, mock_slot_lifecycle):
        """Summary slot should terminate with full content."""
        await mock_slot_lifecycle.terminate_slot(
            room_id="room_1",
            turn_id="turn_1",
            slot_id="summary-user_msg_1",
            status="completed",
            content="This is the synthesis summary.",
        )
        mock_slot_lifecycle.terminate_slot.assert_called_once()
        call_args = mock_slot_lifecycle.terminate_slot.call_args
        assert call_args.kwargs["content"] == "This is the synthesis summary."
        assert call_args.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_summary_slot_skipped_when_no_turn_id(self, mock_slot_lifecycle):
        """Summary slot events should be skipped for old messages without turn_id."""
        turn_id = None
        if turn_id:
            await mock_slot_lifecycle.open_slot(
                room_id="room_1",
                turn_id=turn_id,
                slot_id="summary-user_msg_1",
                slot_type="summary",
            )
        mock_slot_lifecycle.open_slot.assert_not_called()

    @pytest.mark.asyncio
    async def test_summary_slot_debate_mode(self, mock_slot_lifecycle):
        """Summary slot for debate mode should use mode='debate'."""
        await mock_slot_lifecycle.open_slot(
            room_id="room_1",
            turn_id="turn_1",
            slot_id="summary-user_msg_1",
            slot_type="summary",
            mode="debate",
        )
        call_args = mock_slot_lifecycle.open_slot.call_args
        assert call_args.kwargs["mode"] == "debate"
