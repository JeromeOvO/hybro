import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.is_connected = True
    redis.exists = AsyncMock(return_value=False)
    redis.set_with_ttl = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def mock_seq_counter():
    counter = MagicMock()
    counter.next = AsyncMock(side_effect=range(1, 100))
    counter.reset = AsyncMock()
    return counter


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.turn_exists = AsyncMock(return_value=True)
    db.append_turn_event = AsyncMock()
    return db


@pytest.fixture
def mock_sse():
    sse = MagicMock()
    sse.broadcast_turn_event = AsyncMock()
    return sse


@pytest.fixture
def appender(mock_sse, mock_db, mock_seq_counter, mock_redis):
    from services.turn_event_service import TurnEventAppender

    return TurnEventAppender(
        sse_manager=mock_sse,
        db_service=mock_db,
        seq_counter=mock_seq_counter,
        redis=mock_redis,
        dual_write_mode=True,
    )


class TestStartTurn:
    @pytest.mark.asyncio
    async def test_start_turn_returns_event(self, appender, mock_seq_counter):
        event = await appender.start_turn(
            room_id="room_1",
            turn_id="turn_1",
            user_input={"text": "hello"},
            client_request_id="req_abc",
        )
        assert event is not None
        assert event.type == "turn_started"
        assert event.turn_id == "turn_1"
        assert event.client_request_id == "req_abc"
        mock_seq_counter.reset.assert_called_once_with("turn_1")

    @pytest.mark.asyncio
    async def test_start_turn_persists_event(self, appender, mock_db):
        await appender.start_turn("room_1", "turn_1", {"text": "hi"}, "req_1")
        mock_db.append_turn_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_turn_broadcasts_event(self, appender, mock_sse):
        await appender.start_turn("room_1", "turn_1", {"text": "hi"}, "req_1")
        mock_sse.broadcast_turn_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_turn_failure_disables_journal_in_dual_write(
        self, mock_sse, mock_db, mock_redis
    ):
        from services.turn_event_service import TurnEventAppender

        bad_counter = MagicMock()
        bad_counter.reset = AsyncMock(side_effect=Exception("Redis down"))

        appender = TurnEventAppender(
            mock_sse, mock_db, bad_counter, mock_redis, dual_write_mode=True
        )
        result = await appender.start_turn("room_1", "turn_1", {"text": "hi"}, "req_1")
        assert result is None
        mock_redis.set_with_ttl.assert_called_with(
            "turn_journal_disabled:turn_1", "1", ex=7200
        )

    @pytest.mark.asyncio
    async def test_start_turn_failure_raises_in_phase3(
        self, mock_sse, mock_db, mock_redis
    ):
        from services.turn_event_service import TurnEventAppender

        bad_counter = MagicMock()
        bad_counter.reset = AsyncMock(side_effect=Exception("Redis down"))

        appender = TurnEventAppender(
            mock_sse, mock_db, bad_counter, mock_redis, dual_write_mode=False
        )
        with pytest.raises(Exception, match="Redis down"):
            await appender.start_turn("room_1", "turn_1", {"text": "hi"}, "req_1")


class TestAppend:
    @pytest.mark.asyncio
    async def test_append_persists_by_default(self, appender, mock_db):
        event = await appender.append(
            "room_1", "turn_1", "slot_opened",
            {"slot_id": "msg_1", "slot_type": "agent", "agent_id": "a1", "agent_name": "A"},
        )
        assert event is not None
        mock_db.append_turn_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_append_skips_persist_when_false(self, appender, mock_db):
        await appender.append(
            "room_1", "turn_1", "slot_delta",
            {"slot_id": "msg_1", "text_delta": "hello"},
            persist=False,
        )
        mock_db.append_turn_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_append_skips_for_journal_disabled_turn(
        self, mock_sse, mock_db, mock_seq_counter, mock_redis
    ):
        from services.turn_event_service import TurnEventAppender

        mock_redis.exists = AsyncMock(return_value=True)  # journal disabled
        appender = TurnEventAppender(
            mock_sse, mock_db, mock_seq_counter, mock_redis, dual_write_mode=True
        )
        result = await appender.append(
            "room_1", "turn_1", "slot_opened",
            {"slot_id": "msg_1", "slot_type": "agent"},
        )
        assert result is None
        mock_db.append_turn_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_append_raises_if_turn_not_started(self, appender, mock_db):
        mock_db.turn_exists = AsyncMock(return_value=False)
        # In dual_write_mode, failure disables journal
        result = await appender.append(
            "room_1", "turn_nonexistent", "slot_opened",
            {"slot_id": "msg_1", "slot_type": "agent"},
        )
        assert result is None  # graceful in dual_write

    @pytest.mark.asyncio
    async def test_append_failure_disables_journal_in_dual_write(
        self, mock_sse, mock_db, mock_seq_counter, mock_redis
    ):
        from services.turn_event_service import TurnEventAppender

        mock_db.append_turn_event = AsyncMock(side_effect=Exception("DB error"))
        appender = TurnEventAppender(
            mock_sse, mock_db, mock_seq_counter, mock_redis, dual_write_mode=True
        )
        result = await appender.append(
            "room_1", "turn_1", "phase_changed",
            {"phase": {"name": "planning"}},
        )
        assert result is None
        mock_redis.set_with_ttl.assert_called()
