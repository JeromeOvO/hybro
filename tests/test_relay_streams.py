from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hub_runtime_bridge.transport.relay_streams import RelayStreamService


@pytest.mark.asyncio
async def test_relay_stream_service_uses_dal_streams_and_kv() -> None:
    streams = MagicMock()
    streams.is_connected = True
    streams.xadd = AsyncMock(return_value="1-0")
    kv = MagicMock()
    kv.is_connected = True
    kv.set = AsyncMock(return_value=None)
    kv.exists = AsyncMock(return_value=True)

    service = RelayStreamService(streams, kv=kv, maxlen=50, heartbeat_ttl=9)

    assert service.is_connected is True
    assert await service.push_event("hub-1", {"type": "user_message"}) == "1-0"
    await service.record_heartbeat("hub-1")
    assert await service.is_hub_alive("hub-1") is True

    streams.xadd.assert_awaited_once()
    kv.set.assert_awaited_once_with("hub:heartbeat:hub-1", "1", ttl=9)
    kv.exists.assert_awaited_once_with("hub:heartbeat:hub-1")


def test_relay_stream_service_requires_connected_streams_and_kv() -> None:
    streams = MagicMock()
    streams.is_connected = True
    kv = MagicMock()
    kv.is_connected = False

    service = RelayStreamService(streams, kv=kv)

    assert service.is_connected is False
