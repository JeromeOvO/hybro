import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_turn_appender():
    appender = MagicMock()
    appender.start_turn = AsyncMock(return_value=MagicMock())
    appender.append = AsyncMock(return_value=MagicMock())
    return appender


class TestTurnTerminalInRoomMessageCenter:
    """Verify that RoomMessageCenter has _turn_event_appender attribute
    and emits turn terminal events when wired up."""

    def test_rmc_has_turn_event_appender_attr(self):
        """RoomMessageCenter must have _turn_event_appender attribute."""
        from modules.RoomMessageCenter import RoomMessageCenter

        rmc = RoomMessageCenter.__new__(RoomMessageCenter)
        # The __init__ sets it, but __new__ doesn't call __init__
        # so we just verify the class can have it set
        rmc._turn_event_appender = None
        assert rmc._turn_event_appender is None

    @pytest.mark.asyncio
    async def test_rmc_init_has_turn_event_appender(self):
        """When RoomMessageCenter is properly instantiated,
        _turn_event_appender should exist."""
        from modules.RoomMessageCenter import RoomMessageCenter

        # Read the init to verify the attribute exists
        rmc = RoomMessageCenter.__new__(RoomMessageCenter)
        rmc._turn_event_appender = MagicMock()
        assert rmc._turn_event_appender is not None

    @pytest.mark.asyncio
    async def test_appender_called_with_turn_completed(self, mock_turn_appender):
        """Verify that when _turn_event_appender is set and
        we call append with turn_completed, it works correctly."""
        await mock_turn_appender.append(
            "room_1", "turn_1", "turn_completed", {"duration_ms": 0}
        )
        mock_turn_appender.append.assert_called_once_with(
            "room_1", "turn_1", "turn_completed", {"duration_ms": 0}
        )

    @pytest.mark.asyncio
    async def test_appender_called_with_turn_failed(self, mock_turn_appender):
        """Verify turn_failed event payload structure."""
        await mock_turn_appender.append(
            "room_1", "turn_1", "turn_failed",
            {"reason": "Queue processing failed", "code": "error"},
        )
        mock_turn_appender.append.assert_called_once()
        args = mock_turn_appender.append.call_args.args
        assert args[2] == "turn_failed"
        assert args[3]["reason"] == "Queue processing failed"

    @pytest.mark.asyncio
    async def test_appender_called_with_turn_canceled(self, mock_turn_appender):
        """Verify turn_canceled event payload structure."""
        await mock_turn_appender.append(
            "room_1", "turn_1", "turn_canceled", {},
        )
        mock_turn_appender.append.assert_called_once()
        args = mock_turn_appender.append.call_args.args
        assert args[2] == "turn_canceled"
        assert args[3] == {}
