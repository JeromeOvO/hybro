"""Unit tests for DAL Redis key-value and stream primitives."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_client() -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock(return_value="test_value")
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.incrby = AsyncMock(return_value=3)
    client.exists = AsyncMock(return_value=1)
    client.ping = AsyncMock(return_value=True)
    client.aclose = AsyncMock()
    return client


@pytest.mark.asyncio
class TestRedisKVImpl:
    async def test_setnx_uses_dal_method_name_and_ttl(self):
        from dal.redis.kv import RedisKVImpl

        client = _make_client()
        kv = RedisKVImpl(client=client)

        assert await kv.setnx("test_key", "test_value", ttl=60) is True

        client.set.assert_awaited_once_with(
            "test_key", "test_value", nx=True, ex=60
        )

    async def test_basic_key_value_operations_use_direct_client(self):
        from dal.redis.kv import RedisKVImpl

        client = _make_client()
        kv = RedisKVImpl(client=client)

        assert await kv.get("test_key") == "test_value"
        await kv.set("test_key", "test_value", ttl=120)
        assert await kv.exists("test_key") is True
        assert await kv.delete("test_key") is True
        assert await kv.increment("counter", amount=2) == 3

        client.get.assert_awaited_once_with("test_key")
        client.set.assert_awaited_once_with("test_key", "test_value", ex=120)
        client.exists.assert_awaited_once_with("test_key")
        client.delete.assert_awaited_once_with("test_key")
        client.incrby.assert_awaited_once_with("counter", 2)

    async def test_health_tracks_successful_ping_and_close(self):
        from dal.redis.kv import RedisKVImpl

        client = _make_client()
        kv = RedisKVImpl(client=client)

        assert kv.is_connected is True
        assert await kv.ping() is True
        assert kv.is_connected is True

        await kv.close()

        client.aclose.assert_awaited_once()
        assert kv.is_connected is False

    async def test_failed_ping_clears_client_and_health(self):
        from dal.redis.kv import RedisKVImpl

        client = _make_client()
        client.ping = AsyncMock(side_effect=ConnectionError("down"))
        kv = RedisKVImpl(client=client)

        assert await kv.ping() is False

        assert kv.is_connected is False
        assert kv._client is None

    async def test_degrades_without_url(self, monkeypatch):
        from dal.redis import kv as kv_module

        monkeypatch.setattr(kv_module.settings, "redis_url", "")

        kv = kv_module.RedisKVImpl()

        assert kv.is_connected is False
        assert await kv.get("test_key") is None
        await kv.set("test_key", "test_value")
        assert await kv.setnx("test_key", "test_value", ttl=60) is False
        assert await kv.exists("test_key") is False
        assert await kv.delete("test_key") is False
        assert await kv.increment("counter") == 0
        assert await kv.ping() is False


@pytest.mark.asyncio
class TestRedisStreamsImpl:
    async def test_xadd_and_xread_use_dal_shape(self):
        from dal.redis.streams import RedisStreamsImpl

        client = MagicMock()
        client.xadd = AsyncMock(return_value="1234567890-0")
        client.xread = AsyncMock(
            return_value=[
                (
                    "test_stream",
                    [
                        ("1234567890-0", {"field1": "value1"}),
                        ("1234567890-1", {"field2": "value2"}),
                    ],
                )
            ]
        )

        streams = RedisStreamsImpl(client=client)

        assert (
            await streams.xadd("test_stream", {"field1": "value1"}, maxlen=1000)
            == "1234567890-0"
        )
        assert await streams.xread({"test_stream": "0"}, count=10, block=5000) == [
            {
                "stream": "test_stream",
                "id": "1234567890-0",
                "fields": {"field1": "value1"},
            },
            {
                "stream": "test_stream",
                "id": "1234567890-1",
                "fields": {"field2": "value2"},
            },
        ]

        client.xadd.assert_awaited_once_with(
            "test_stream", {"field1": "value1"}, maxlen=1000
        )
        client.xread.assert_awaited_once_with(
            {"test_stream": "0"}, block=5000, count=10
        )

    async def test_health_tracks_successful_ping_and_close(self):
        from dal.redis.streams import RedisStreamsImpl

        client = MagicMock()
        client.ping = AsyncMock(return_value=True)
        client.aclose = AsyncMock()
        streams = RedisStreamsImpl(client=client)

        assert streams.is_connected is True
        assert await streams.ping() is True
        assert streams.is_connected is True

        await streams.close()

        client.aclose.assert_awaited_once()
        assert streams.is_connected is False

    async def test_failed_ping_clears_client_and_health(self):
        from dal.redis.streams import RedisStreamsImpl

        client = MagicMock()
        client.ping = AsyncMock(side_effect=ConnectionError("down"))
        streams = RedisStreamsImpl(client=client)

        assert await streams.ping() is False

        assert streams.is_connected is False
        assert streams._client is None

    async def test_degrades_without_url(self, monkeypatch):
        from dal.redis import streams as streams_module

        monkeypatch.setattr(streams_module.settings, "redis_url", "")

        streams = streams_module.RedisStreamsImpl()

        assert streams.is_connected is False
        assert await streams.xadd("test_stream", {"field": "value"}) == ""
        assert await streams.xread({"test_stream": "0"}) == []
        assert await streams.ping() is False


@pytest.mark.asyncio
async def test_relay_stream_service_uses_command_client_for_heartbeat():
    from hub_runtime_bridge.transport.relay_streams import RelayStreamService

    streams_client = MagicMock()
    streams_client.is_connected = True
    command_client = MagicMock()
    command_client.is_connected = True
    command_client.set = AsyncMock(return_value=True)
    command_client.exists = AsyncMock(return_value=True)

    relay_streams = RelayStreamService(
        streams_client,
        kv=command_client,
        heartbeat_ttl=17,
    )

    await relay_streams.record_heartbeat("hub-1")
    assert await relay_streams.is_hub_alive("hub-1") is True

    command_client.set.assert_awaited_once_with(
        "hub:heartbeat:hub-1",
        "1",
        ttl=17,
    )
    command_client.exists.assert_awaited_once_with("hub:heartbeat:hub-1")


@pytest.mark.asyncio
async def test_relay_stream_service_requires_kv_for_heartbeat():
    from hub_runtime_bridge.transport.relay_streams import RelayStreamService

    redis_client = MagicMock()
    redis_client.is_connected = True
    redis_client.xadd = AsyncMock(return_value="1-0")
    redis_client.xread = AsyncMock(return_value=[])
    redis_client.set = AsyncMock(return_value=True)
    redis_client.exists = AsyncMock(return_value=True)

    relay_streams = RelayStreamService(redis_client, heartbeat_ttl=19)

    await relay_streams.record_heartbeat("hub-1")
    assert await relay_streams.is_hub_alive("hub-1") is False

    redis_client.set.assert_not_awaited()
    redis_client.exists.assert_not_awaited()
