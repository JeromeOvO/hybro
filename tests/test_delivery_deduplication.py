from unittest.mock import AsyncMock

import pytest

from common.errors import TransientError
from delivery.config import DeliveryConfig
from delivery.sse.deduplication import TerminalStatusDeduplicator


class FakeTimer:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeRedisKV:
    def __init__(self, *, setnx_result=True, error: Exception | None = None):
        self.setnx_result = setnx_result
        self.error = error
        self.calls: list[tuple[str, str, int]] = []

    async def setnx(self, key: str, value: str, ttl: int) -> bool:
        self.calls.append((key, value, ttl))
        if self.error is not None:
            raise self.error
        return self.setnx_result


@pytest.mark.asyncio
async def test_non_terminal_statuses_are_never_deduped():
    redis = FakeRedisKV(setnx_result=False)
    dedup = TerminalStatusDeduplicator(config=DeliveryConfig(), redis_kv=redis)

    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="processing",
    )
    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="awaiting_input",
    )
    assert redis.calls == []


@pytest.mark.asyncio
async def test_first_terminal_status_passes_and_second_l1_hit_suppresses():
    redis = FakeRedisKV(setnx_result=True)
    dedup = TerminalStatusDeduplicator(config=DeliveryConfig(), redis_kv=redis)

    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )
    assert not await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="failed",
    )

    assert redis.calls == [("terminal:room-1:msg-1", "completed", 300)]


@pytest.mark.asyncio
async def test_l2_redis_nx_miss_suppresses_terminal_status():
    redis = FakeRedisKV(setnx_result=False)
    dedup = TerminalStatusDeduplicator(config=DeliveryConfig(), redis_kv=redis)

    assert not await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )
    assert redis.calls == [("terminal:room-1:msg-1", "completed", 300)]


@pytest.mark.asyncio
async def test_custom_terminal_prefix_and_ttl_are_used_for_l2():
    redis = FakeRedisKV(setnx_result=True)
    config = DeliveryConfig(
        redis_terminal_key_prefix="termx:",
        terminal_dedup_ttl_seconds=7,
    )
    dedup = TerminalStatusDeduplicator(config=config, redis_kv=redis)

    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )

    assert redis.calls == [("termx:room-1:msg-1", "completed", 7)]


@pytest.mark.asyncio
async def test_l1_ttl_uses_custom_config():
    timer = FakeTimer()
    redis = FakeRedisKV(setnx_result=True)
    config = DeliveryConfig(terminal_dedup_ttl_seconds=7)
    dedup = TerminalStatusDeduplicator(config=config, redis_kv=redis, timer=timer)

    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )
    assert not await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )

    timer.advance(8)
    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )

    assert len(redis.calls) == 2


@pytest.mark.asyncio
async def test_custom_terminal_status_set_controls_dedup():
    redis = FakeRedisKV(setnx_result=True)
    config = DeliveryConfig(terminal_processing_statuses=frozenset({"done"}))
    dedup = TerminalStatusDeduplicator(config=config, redis_kv=redis)

    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )
    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="done",
    )
    assert redis.calls == [("terminal:room-1:msg-1", "done", 300)]


@pytest.mark.asyncio
async def test_redis_failure_degrades_to_l1_only_without_escaping():
    redis = FakeRedisKV(error=TransientError("redis down"))
    dedup = TerminalStatusDeduplicator(config=DeliveryConfig(), redis_kv=redis)

    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )
    assert not await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="failed",
    )


@pytest.mark.asyncio
async def test_missing_redis_uses_l1_only_and_missing_message_id_never_dedups():
    dedup = TerminalStatusDeduplicator(config=DeliveryConfig(), redis_kv=None)

    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )
    assert not await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )
    assert await dedup.should_deliver(
        room_id="room-1",
        message_id=None,
        status="completed",
    )
    assert await dedup.should_deliver(
        room_id="room-1",
        message_id=None,
        status="completed",
    )


def test_l1_cache_uses_configured_maxsize_and_ttl():
    config = DeliveryConfig(
        terminal_dedup_cache_maxsize=2,
        terminal_dedup_ttl_seconds=7,
    )
    dedup = TerminalStatusDeduplicator(config=config, redis_kv=None)

    assert dedup.cache.maxsize == 2
    assert dedup.cache.ttl == 7


@pytest.mark.asyncio
async def test_default_legacy_terminal_statuses_are_deduped():
    redis = FakeRedisKV(setnx_result=True)
    dedup = TerminalStatusDeduplicator(config=DeliveryConfig(), redis_kv=redis)

    for status in ["rejected", "rate_limited", "error"]:
        assert await dedup.should_deliver(
            room_id="room-1",
            message_id=status,
            status=status,
        )

    assert len(redis.calls) == 3
