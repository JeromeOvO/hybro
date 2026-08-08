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
        self.values: dict[str, str] = {}
        self.compare_deleted: list[tuple[str, str]] = []

    async def setnx(self, key: str, value: str, ttl: int) -> bool:
        self.calls.append((key, value, ttl))
        if self.error is not None:
            raise self.error
        if self.setnx_result:
            self.values[key] = value
        return self.setnx_result

    async def compare_delete(self, key: str, expected_value: str) -> bool:
        self.compare_deleted.append((key, expected_value))
        if self.error is not None:
            raise self.error
        if self.values.get(key) != expected_value:
            return False
        self.values.pop(key)
        return True


class SharedNXRedisKV(FakeRedisKV):
    def __init__(self):
        super().__init__()

    async def setnx(self, key: str, value: str, ttl: int) -> bool:
        self.calls.append((key, value, ttl))
        if key in self.values:
            return False
        self.values[key] = value
        return True


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


@pytest.mark.core
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

    assert len(redis.calls) == 1
    assert redis.calls[0][0::2] == ("terminal:room-1:msg-1", 300)
    assert redis.calls[0][1] != "completed"


@pytest.mark.asyncio
async def test_l2_redis_nx_miss_suppresses_terminal_status():
    redis = FakeRedisKV(setnx_result=False)
    dedup = TerminalStatusDeduplicator(config=DeliveryConfig(), redis_kv=redis)

    assert not await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )
    assert len(redis.calls) == 1
    assert redis.calls[0][0::2] == ("terminal:room-1:msg-1", 300)
    assert dedup.cache.get("room-1:msg-1") is None


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

    assert len(redis.calls) == 1
    assert redis.calls[0][0::2] == ("termx:room-1:msg-1", 7)


@pytest.mark.asyncio
async def test_failed_delivery_release_clears_owned_l1_and_l2_reservations():
    redis = FakeRedisKV(setnx_result=True)
    dedup = TerminalStatusDeduplicator(config=DeliveryConfig(), redis_kv=redis)

    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )
    await dedup.release(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )

    assert "room-1:msg-1" not in dedup.cache
    assert redis.compare_deleted == [("terminal:room-1:msg-1", redis.calls[0][1])]
    assert await dedup.should_deliver(
        room_id="room-1",
        message_id="msg-1",
        status="completed",
    )


@pytest.mark.asyncio
async def test_release_does_not_delete_a_reclaimed_reservation():
    redis = FakeRedisKV(setnx_result=True)
    dedup = TerminalStatusDeduplicator(
        config=DeliveryConfig(),
        redis_kv=redis,
        claim_id_factory=lambda: "owner-1",
    )

    assert await dedup.should_deliver(
        room_id="room-1", message_id="msg-1", status="completed"
    )
    redis.values["terminal:room-1:msg-1"] = "owner-2"

    await dedup.release(room_id="room-1", message_id="msg-1", status="completed")

    assert redis.values["terminal:room-1:msg-1"] == "owner-2"
    assert redis.compare_deleted == [("terminal:room-1:msg-1", "owner-1")]


@pytest.mark.asyncio
async def test_multi_instance_loser_does_not_poison_l1_after_owner_release():
    redis = SharedNXRedisKV()
    owner = TerminalStatusDeduplicator(
        config=DeliveryConfig(),
        redis_kv=redis,
        claim_id_factory=lambda: "owner",
    )
    loser = TerminalStatusDeduplicator(
        config=DeliveryConfig(),
        redis_kv=redis,
        claim_id_factory=lambda: "loser",
    )

    assert await owner.should_deliver(
        room_id="room-1", message_id="msg-1", status="completed"
    )
    assert not await loser.should_deliver(
        room_id="room-1", message_id="msg-1", status="completed"
    )
    assert "room-1:msg-1" not in loser.cache

    await owner.release(room_id="room-1", message_id="msg-1", status="completed")
    assert await loser.should_deliver(
        room_id="room-1", message_id="msg-1", status="completed"
    )


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
    assert len(redis.calls) == 1
    assert redis.calls[0][0::2] == ("terminal:room-1:msg-1", 300)


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
