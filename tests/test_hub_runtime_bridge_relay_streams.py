from __future__ import annotations

import json

import pytest

from common.errors import TransientError
from hub_runtime_bridge.transport.offline_queue import OfflineQueue
from hub_runtime_bridge.transport.relay_streams import RelayStreamService


class Streams:
    is_connected = True

    def __init__(self) -> None:
        self.entries = []

    async def xadd(self, stream, payload, maxlen):
        self.entries.append((stream, payload, maxlen))
        return "1-0"

    async def xread(self, streams, count, block):
        stream = next(iter(streams))
        return [
            {
                "stream": stream,
                "id": "1-0",
                "fields": {"payload": json.dumps({"type": "user_message"})},
            }
        ]


class RedisPyStreams:
    is_connected = True

    async def xread(self, streams, count, block):
        stream = next(iter(streams))
        return [
            (
                stream,
                [
                    (
                        "1-0",
                        {"payload": json.dumps({"type": "user_message"})},
                    )
                ],
            )
        ]


class KV:
    is_connected = True

    def __init__(self) -> None:
        self.keys = set()

    async def set(self, key, value, ttl):
        self.keys.add(key)
        return None

    async def exists(self, key):
        return key in self.keys


@pytest.mark.asyncio
async def test_relay_stream_payload_and_heartbeat_ttl_parity() -> None:
    stream_store = Streams()
    kv = KV()
    streams = RelayStreamService(stream_store, kv=kv, maxlen=50, heartbeat_ttl=9)

    entry_id = await streams.push_event("hub-1", {"type": "user_message"})
    entries = await streams.read_events("hub-1", last_id="0-0")
    await streams.record_heartbeat("hub-1")

    assert entry_id == "1-0"
    assert entries == [("1-0", {"type": "user_message"})]
    assert await streams.is_hub_alive("hub-1") is True


@pytest.mark.asyncio
async def test_relay_streams_read_redis_py_tuple_rows() -> None:
    streams = RelayStreamService(RedisPyStreams())

    entries = await streams.read_events("hub-1", last_id="0-0")

    assert entries == [("1-0", {"type": "user_message"})]


class BrokenStreams:
    is_connected = True

    async def xadd(self, *_args, **_kwargs):
        raise TransientError("xadd failed")

    async def xread(self, *_args, **_kwargs):
        raise TransientError("xread failed")


class BrokenKV:
    is_connected = True

    async def set(self, *_args, **_kwargs):
        raise TransientError("set failed")

    async def exists(self, *_args, **_kwargs):
        raise TransientError("exists failed")


@pytest.mark.asyncio
async def test_relay_streams_degrade_when_redis_raises() -> None:
    streams = RelayStreamService(BrokenStreams(), kv=BrokenKV())

    assert await streams.push_event("hub-1", {"type": "user_message"}) is None
    assert await streams.read_events("hub-1") == []
    await streams.record_heartbeat("hub-1")
    assert await streams.is_hub_alive("hub-1") is False


def test_offline_queue_ttl_and_overflow() -> None:
    now = [0.0]
    queue = OfflineQueue(max_size=1, ttl_seconds=5, clock=lambda: now[0])
    assert queue.append({"id": 1}) is None
    assert queue.append({"id": 2}) == {"id": 1}
    now[0] = 10
    assert queue.sweep_expired() == [{"id": 2}]
