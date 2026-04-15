import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.is_connected = True
    redis.incr = AsyncMock(side_effect=[1, 2, 3, 4, 5])
    redis.set_with_ttl = AsyncMock(return_value=True)
    return redis


@pytest.mark.asyncio
async def test_next_returns_incrementing_seq(mock_redis):
    from services.turn_event_service import TurnSeqCounter

    counter = TurnSeqCounter(mock_redis)
    assert await counter.next("turn_1") == 1
    assert await counter.next("turn_1") == 2
    assert await counter.next("turn_1") == 3
    mock_redis.incr.assert_called_with("turn_seq:turn_1")


@pytest.mark.asyncio
async def test_reset_sets_key_to_zero(mock_redis):
    from services.turn_event_service import TurnSeqCounter

    counter = TurnSeqCounter(mock_redis)
    await counter.reset("turn_1")
    mock_redis.set_with_ttl.assert_called_once_with("turn_seq:turn_1", "0", ex=7200)


@pytest.mark.asyncio
async def test_next_uses_turn_scoped_key(mock_redis):
    from services.turn_event_service import TurnSeqCounter

    counter = TurnSeqCounter(mock_redis)
    await counter.next("turn_abc")
    mock_redis.incr.assert_called_with("turn_seq:turn_abc")
