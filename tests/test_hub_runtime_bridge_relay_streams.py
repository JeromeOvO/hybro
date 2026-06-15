from __future__ import annotations

import json

import pytest

from hub_runtime_bridge.transport.offline_queue import OfflineQueue
from hub_runtime_bridge.transport.relay_streams import RelayStreamService


class Redis:
    is_connected = True

    def __init__(self) -> None:
        self.entries = []
        self.keys = set()

    async def xadd(self, stream, payload, maxlen):
        self.entries.append((stream, payload, maxlen))
        return "1-0"

    async def xread(self, streams, count, block):
        stream = next(iter(streams))
        return [(stream, [("1-0", {"payload": json.dumps({"type": "user_message"})})])]

    async def set_with_ttl(self, key, value, ex):
        self.keys.add(key)
        return True

    async def exists(self, key):
        return key in self.keys


@pytest.mark.asyncio
async def test_relay_stream_payload_and_heartbeat_ttl_parity() -> None:
    redis = Redis()
    streams = RelayStreamService(redis, maxlen=50, heartbeat_ttl=9)

    entry_id = await streams.push_event("hub-1", {"type": "user_message"})
    entries = await streams.read_events("hub-1", last_id="0-0")
    await streams.record_heartbeat("hub-1")

    assert entry_id == "1-0"
    assert entries == [("1-0", {"type": "user_message"})]
    assert await streams.is_hub_alive("hub-1") is True


def test_offline_queue_ttl_and_overflow() -> None:
    now = [0.0]
    queue = OfflineQueue(max_size=1, ttl_seconds=5, clock=lambda: now[0])
    assert queue.append({"id": 1}) is None
    assert queue.append({"id": 2}) == {"id": 1}
    now[0] = 10
    assert queue.sweep_expired() == [{"id": 2}]
