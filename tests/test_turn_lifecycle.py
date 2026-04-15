import pytest
from unittest.mock import AsyncMock, MagicMock
from models.turn_event import TurnEvent, TurnStartedPayload


class TestTurnStartedEmission:
    """Test that TurnEventAppender.start_turn creates a valid turn_started event."""

    @pytest.mark.asyncio
    async def test_start_turn_creates_turn_started_event(self):
        """start_turn() returns a TurnEvent with type=turn_started."""
        mock_redis = MagicMock()
        mock_redis.is_connected = True
        mock_redis.exists = AsyncMock(return_value=False)
        mock_redis.set_with_ttl = AsyncMock(return_value=True)

        mock_seq = MagicMock()
        mock_seq.reset = AsyncMock()
        mock_seq.next = AsyncMock(return_value=1)

        mock_db = MagicMock()
        mock_db.append_turn_event = AsyncMock()

        mock_sse = MagicMock()
        mock_sse.broadcast_turn_event = AsyncMock()

        from services.turn_event_service import TurnEventAppender

        appender = TurnEventAppender(
            sse_manager=mock_sse,
            db_service=mock_db,
            seq_counter=mock_seq,
            redis=mock_redis,
            dual_write_mode=True,
        )

        event = await appender.start_turn(
            room_id="room_1",
            turn_id="user_msg_123",
            user_input={"text": "hello", "attachments": []},
            client_request_id="req_abc",
        )

        assert event is not None
        assert event.type == "turn_started"
        assert event.turn_id == "user_msg_123"
        assert event.seq == 1
        assert event.client_request_id == "req_abc"
        mock_seq.reset.assert_called_once_with("user_msg_123")
        mock_db.append_turn_event.assert_called_once()
        mock_sse.broadcast_turn_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_turn_user_input_preserved(self):
        """start_turn() preserves user_input in the event payload."""
        mock_redis = MagicMock()
        mock_redis.is_connected = True
        mock_redis.exists = AsyncMock(return_value=False)
        mock_redis.set_with_ttl = AsyncMock(return_value=True)

        mock_seq = MagicMock()
        mock_seq.reset = AsyncMock()
        mock_seq.next = AsyncMock(return_value=1)

        mock_db = MagicMock()
        mock_db.append_turn_event = AsyncMock()

        mock_sse = MagicMock()
        mock_sse.broadcast_turn_event = AsyncMock()

        from services.turn_event_service import TurnEventAppender

        appender = TurnEventAppender(
            mock_sse, mock_db, mock_seq, mock_redis, dual_write_mode=True
        )

        user_input = {"text": "complex query", "attachments": [{"name": "file.pdf"}]}
        event = await appender.start_turn(
            room_id="room_1",
            turn_id="turn_42",
            user_input=user_input,
            client_request_id="req_42",
        )

        assert event.payload.user_input == user_input

    @pytest.mark.asyncio
    async def test_start_turn_broadcasts_wire_format(self):
        """The SSE broadcast should receive the flat wire format."""
        mock_redis = MagicMock()
        mock_redis.is_connected = True
        mock_redis.exists = AsyncMock(return_value=False)
        mock_redis.set_with_ttl = AsyncMock(return_value=True)

        mock_seq = MagicMock()
        mock_seq.reset = AsyncMock()
        mock_seq.next = AsyncMock(return_value=1)

        mock_db = MagicMock()
        mock_db.append_turn_event = AsyncMock()

        mock_sse = MagicMock()
        mock_sse.broadcast_turn_event = AsyncMock()

        from services.turn_event_service import TurnEventAppender

        appender = TurnEventAppender(
            mock_sse, mock_db, mock_seq, mock_redis, dual_write_mode=True
        )

        await appender.start_turn(
            room_id="room_1",
            turn_id="turn_1",
            user_input={"text": "hi"},
            client_request_id="req_1",
        )

        # broadcast_turn_event receives the TurnEvent object
        broadcast_call = mock_sse.broadcast_turn_event.call_args
        assert broadcast_call.args[0] == "room_1"
        event = broadcast_call.args[1]
        assert event.type == "turn_started"
