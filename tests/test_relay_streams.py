"""Tests for RelayStreamService."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from infrastructure.relay_streams import RelayStreamService


def _make_redis():
    redis = MagicMock()
    redis.xadd = AsyncMock()
    redis.xread = AsyncMock()
    redis.set_with_ttl = AsyncMock()
    redis.exists = AsyncMock()
    return redis


@pytest.mark.asyncio
class TestRelayStreamPushRead:
    async def test_push_event_calls_xadd(self):
        redis = _make_redis()
        redis.xadd.return_value = "1-0"
        streams = RelayStreamService(redis)
        entry_id = await streams.push_event("hub-1", {"type": "task"})
        assert entry_id == "1-0"
        redis.xadd.assert_called_once()
        call_args = redis.xadd.call_args
        assert call_args[0][0] == "hub:relay:hub-1"

    async def test_push_event_uses_maxlen(self):
        redis = _make_redis()
        redis.xadd.return_value = "1-0"
        streams = RelayStreamService(redis, maxlen=5000)
        await streams.push_event("hub-1", {"type": "task"})
        call_args = redis.xadd.call_args
        assert call_args[1].get("maxlen") == 5000 or call_args[0][2] == 5000

    async def test_push_event_returns_none_on_error(self):
        redis = _make_redis()
        redis.xadd.return_value = None
        streams = RelayStreamService(redis)
        result = await streams.push_event("hub-1", {"type": "task"})
        assert result is None

    async def test_read_events_returns_parsed_entries(self):
        redis = _make_redis()
        redis.xread.return_value = [
            ("hub:relay:hub-1", [
                ("1-0", {"payload": '{"type":"task_dispatch"}'}),
                ("2-0", {"payload": '{"type":"heartbeat"}'}),
            ])
        ]
        streams = RelayStreamService(redis)
        entries = await streams.read_events("hub-1")
        assert len(entries) == 2
        assert entries[0] == ("1-0", {"type": "task_dispatch"})
        assert entries[1] == ("2-0", {"type": "heartbeat"})

    async def test_read_events_with_last_id(self):
        redis = _make_redis()
        redis.xread.return_value = []
        streams = RelayStreamService(redis)
        await streams.read_events("hub-1", last_id="5-0")
        call_args = redis.xread.call_args
        assert call_args[0][0] == {"hub:relay:hub-1": "5-0"}

    async def test_read_events_returns_empty_on_timeout(self):
        redis = _make_redis()
        redis.xread.return_value = None
        streams = RelayStreamService(redis)
        entries = await streams.read_events("hub-1")
        assert entries == []

    async def test_read_events_returns_empty_on_empty_result(self):
        redis = _make_redis()
        redis.xread.return_value = []
        streams = RelayStreamService(redis)
        entries = await streams.read_events("hub-1")
        assert entries == []


@pytest.mark.asyncio
class TestRelayStreamHeartbeat:
    async def test_record_heartbeat_sets_key_with_ttl(self):
        redis = _make_redis()
        streams = RelayStreamService(redis, heartbeat_ttl=90)
        await streams.record_heartbeat("hub-1")
        redis.set_with_ttl.assert_called_once_with("hub:heartbeat:hub-1", "1", ex=90)

    async def test_record_heartbeat_custom_ttl(self):
        redis = _make_redis()
        streams = RelayStreamService(redis, heartbeat_ttl=120)
        await streams.record_heartbeat("hub-1")
        redis.set_with_ttl.assert_called_once_with("hub:heartbeat:hub-1", "1", ex=120)

    async def test_is_hub_alive_returns_true(self):
        redis = _make_redis()
        redis.exists.return_value = True
        streams = RelayStreamService(redis)
        assert await streams.is_hub_alive("hub-1") is True
        redis.exists.assert_called_once_with("hub:heartbeat:hub-1")

    async def test_is_hub_alive_returns_false(self):
        redis = _make_redis()
        redis.exists.return_value = False
        streams = RelayStreamService(redis)
        assert await streams.is_hub_alive("hub-1") is False
