import pytest
from unittest.mock import AsyncMock, MagicMock
from models.turn_event import TurnEvent, TurnStartedPayload


@pytest.fixture
def mock_sse_manager():
    from services.sse_services import SSEManager
    manager = SSEManager.__new__(SSEManager)
    manager.broadcast_to_room = AsyncMock()
    return manager


@pytest.mark.asyncio
async def test_broadcast_turn_event_sends_correct_format(mock_sse_manager):
    event = TurnEvent(
        event_id="evt_1",
        turn_id="turn_1",
        seq=1,
        ts=1712880000000,
        type="turn_started",
        payload=TurnStartedPayload(user_input={"text": "hello"}),
        client_request_id="req_abc",
    )
    await mock_sse_manager.broadcast_turn_event("room_1", event)
    mock_sse_manager.broadcast_to_room.assert_called_once()
    call_args = mock_sse_manager.broadcast_to_room.call_args
    assert call_args.args[0] == "room_1"
    assert call_args.args[1] == "turn_event"
    data = call_args.args[2]
    assert data["event_id"] == "evt_1"
    assert data["turn_id"] == "turn_1"
    assert data["type"] == "turn_started"


@pytest.mark.asyncio
async def test_broadcast_turn_event_uses_snake_case(mock_sse_manager):
    from models.turn_event import TurnCompletedPayload
    event = TurnEvent(
        event_id="evt_2",
        turn_id="turn_2",
        seq=2,
        ts=1712880001000,
        type="turn_completed",
        payload=TurnCompletedPayload(duration_ms=5000),
    )
    await mock_sse_manager.broadcast_turn_event("room_1", event)
    data = mock_sse_manager.broadcast_to_room.call_args.args[2]
    assert "event_id" in data
    assert "turn_id" in data
    assert "eventId" not in data
