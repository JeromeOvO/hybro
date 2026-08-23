"""Unit tests for DAL Redis key-value and stream primitives."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_client() -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock(return_value="test_value")
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.eval = AsyncMock(return_value=1)
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

        client.set.assert_awaited_once_with("test_key", "test_value", nx=True, ex=60)

    async def test_basic_key_value_operations_use_direct_client(self):
        from dal.redis.kv import RedisKVImpl

        client = _make_client()
        kv = RedisKVImpl(client=client)

        assert await kv.get("test_key") == "test_value"
        await kv.set("test_key", "test_value", ttl=120)
        assert await kv.exists("test_key") is True
        assert await kv.delete("test_key") is True
        assert await kv.compare_delete("test_key", "owner-1") is True
        assert await kv.increment("counter", amount=2) == 3

        client.get.assert_awaited_once_with("test_key")
        client.set.assert_awaited_once_with("test_key", "test_value", ex=120)
        client.exists.assert_awaited_once_with("test_key")
        client.delete.assert_awaited_once_with("test_key")
        script, key_count, key, owner = client.eval.await_args.args
        assert "redis.call('GET', KEYS[1]) == ARGV[1]" in script
        assert (key_count, key, owner) == (1, "test_key", "owner-1")
        client.incrby.assert_awaited_once_with("counter", 2)

    async def test_compare_set_atomically_confirms_owned_reservation(self):
        from dal.redis.kv import RedisKVImpl

        client = _make_client()
        kv = RedisKVImpl(client=client)

        assert await kv.compare_set(
            "test_key",
            "owner-1",
            "delivered:failed",
            ttl=300,
        )

        script, key_count, key, owner, value, ttl = client.eval.await_args.args
        assert "redis.call('GET', KEYS[1]) == ARGV[1]" in script
        assert "redis.call('SET'" in script
        assert (key_count, key, owner, value, ttl) == (
            1,
            "test_key",
            "owner-1",
            "delivered:failed",
            300,
        )

    async def test_compare_delete_reports_owner_mismatch_without_delete(self):
        from dal.redis.kv import RedisKVImpl

        client = _make_client()
        client.eval = AsyncMock(return_value=0)
        kv = RedisKVImpl(client=client)

        assert await kv.compare_delete("test_key", "wrong-owner") is False

    async def test_compare_delete_wraps_driver_failure_as_transient(self):
        from common.errors import TransientError
        from dal.redis.kv import RedisKVImpl

        client = _make_client()
        client.eval = AsyncMock(side_effect=RuntimeError("eval failed"))
        kv = RedisKVImpl(client=client)

        with pytest.raises(TransientError) as exc_info:
            await kv.compare_delete("test_key", "owner")

        assert exc_info.value.details["operation"] == "compare_delete"

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
        assert await kv.compare_delete("test_key", "owner") is False
        assert not await kv.compare_set(
            "test_key", "owner", "delivered:failed", ttl=300
        )
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
async def test_create_redis_runtime_deps_wires_command_client_to_relay_heartbeat(
    monkeypatch,
):
    import container
    from dal.redis import kv as kv_module
    from dal.redis import lock as lock_module
    from dal.redis import streams as streams_module

    command_client = MagicMock()
    command_client.is_connected = True
    command_client.set = AsyncMock(return_value=True)
    command_client.exists = AsyncMock(return_value=True)
    raw_command_client = object()
    command_client._ensure_client = MagicMock(return_value=raw_command_client)

    streams_client = MagicMock()
    streams_client.is_connected = True

    redis_kv_ctor = MagicMock(return_value=command_client)
    redis_streams_ctor = MagicMock(return_value=streams_client)
    leader_ctor = MagicMock(return_value=MagicMock())
    room_lock_ctor = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(kv_module, "RedisKVImpl", redis_kv_ctor)
    monkeypatch.setattr(streams_module, "RedisStreamsImpl", redis_streams_ctor)
    monkeypatch.setattr(lock_module, "LeaderElectorImpl", leader_ctor)
    monkeypatch.setattr(lock_module, "RoomRedisDistributedLock", room_lock_ctor)

    redis_runtime = container.create_redis_runtime_deps(
        redis_url="redis://unit-test",
        instance_id="worker-1",
    )

    assert redis_runtime.command_client is command_client
    assert redis_runtime.streams_client is streams_client
    redis_kv_ctor.assert_called_once_with(url="redis://unit-test")
    redis_streams_ctor.assert_called_once_with(url="redis://unit-test")
    command_client._ensure_client.assert_called_once_with()
    leader_ctor.assert_called_once_with(
        client=raw_command_client,
        instance_id="worker-1",
    )
    room_lock_ctor.assert_called_once_with(client=raw_command_client)


@pytest.mark.asyncio
async def test_close_redis_runtime_deps_dedupes_shared_underlying_clients():
    import container

    shared_client = object()
    streams_client = object()
    command_client = MagicMock()
    command_client._client = shared_client
    command_client.close = AsyncMock()
    leader = MagicMock()
    leader._client = shared_client
    leader.close = AsyncMock()
    room_lock = MagicMock()
    room_lock._client = shared_client
    room_lock.close = AsyncMock()
    streams = MagicMock()
    streams._client = streams_client
    streams.close = AsyncMock()

    redis_runtime = container.RedisRuntimeDeps(
        command_client=command_client,
        streams_client=streams,
        leader=leader,
        room_lock=room_lock,
    )

    await container.close_redis_runtime_deps(redis_runtime)

    streams.close.assert_awaited_once_with()
    command_client.close.assert_awaited_once_with()
    leader.close.assert_not_awaited()
    room_lock.close.assert_not_awaited()
