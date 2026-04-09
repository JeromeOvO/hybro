"""
Integration tests for distributed room locks against a real Redis instance.

These tests require a running Redis server at localhost:6379.
Run with:  pytest -m integration tests/test_distributed_room_lock_integration.py
Skip with: pytest -m "not integration"
"""

import asyncio

import pytest

from infrastructure.redis_service import RedisService
from modules.RoomMessageCenter import (
    RoomMessageCenter,
    ROOM_LOCK_HOLD_TTL_SECONDS,
)

REDIS_URL = "redis://localhost:6379/0"
LOCK_PREFIX = "room:lock:"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rmc(redis_service: RedisService) -> RoomMessageCenter:
    """Build a minimal RoomMessageCenter wired to a real RedisService."""
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc._room_locks = {}
    rmc._redis = redis_service
    return rmc


@pytest.fixture()
async def redis():
    """Provide a connected RedisService and clean up test keys afterwards."""
    svc = RedisService(url=REDIS_URL)
    await svc.start()
    if not svc.is_connected:
        pytest.skip("Redis not available at localhost:6379")
    yield svc
    # Clean up any test lock keys
    if svc._client:
        keys = [k async for k in svc._client.scan_iter(f"{LOCK_PREFIX}integration-*")]
        if keys:
            await svc._client.delete(*keys)
    await svc.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRealRedisLockPrimitives:
    """Low-level acquire/release against a live Redis."""

    async def test_acquire_creates_key_with_ttl(self, redis: RedisService):
        rmc = _make_rmc(redis)
        room = "integration-ttl-check"

        acquired = await rmc._acquire_distributed_lock(room, "owner-1", ttl=30)
        assert acquired is True

        ttl = await redis._client.ttl(f"{LOCK_PREFIX}{room}")
        assert 0 < ttl <= 30

        value = await redis._client.get(f"{LOCK_PREFIX}{room}")
        assert value == "owner-1"

        # cleanup
        await redis._client.delete(f"{LOCK_PREFIX}{room}")

    async def test_acquire_blocked_by_existing_key(self, redis: RedisService):
        rmc = _make_rmc(redis)
        room = "integration-blocked"

        await redis._client.set(f"{LOCK_PREFIX}{room}", "other-owner", ex=30)

        acquired = await rmc._acquire_distributed_lock(room, "owner-2", ttl=30)
        assert acquired is False

        await redis._client.delete(f"{LOCK_PREFIX}{room}")

    async def test_lua_release_only_deletes_own_key(self, redis: RedisService):
        rmc = _make_rmc(redis)
        room = "integration-lua-release"

        await redis._client.set(f"{LOCK_PREFIX}{room}", "real-owner", ex=30)

        # Wrong owner should NOT delete
        await rmc._release_distributed_lock(room, "wrong-owner")
        still_exists = await redis._client.get(f"{LOCK_PREFIX}{room}")
        assert still_exists == "real-owner"

        # Correct owner SHOULD delete
        await rmc._release_distributed_lock(room, "real-owner")
        gone = await redis._client.get(f"{LOCK_PREFIX}{room}")
        assert gone is None


@pytest.mark.integration
class TestRealRedisRoomLockFlow:
    """Full _acquire_room_lock / _release_room_lock against live Redis."""

    async def test_acquire_and_release_round_trip(self, redis: RedisService):
        rmc = _make_rmc(redis)
        room = "integration-round-trip"

        owner = await rmc._acquire_room_lock(room, timeout=5)
        assert owner is not None

        # Key should exist in Redis
        value = await redis._client.get(f"{LOCK_PREFIX}{room}")
        assert value == owner

        await rmc._release_room_lock(room, owner)

        # Key should be gone after release
        value = await redis._client.get(f"{LOCK_PREFIX}{room}")
        assert value is None

    async def test_ttl_is_hold_duration_not_timeout(self, redis: RedisService):
        rmc = _make_rmc(redis)
        room = "integration-ttl-value"

        owner = await rmc._acquire_room_lock(room, timeout=5)
        assert owner is not None

        ttl = await redis._client.ttl(f"{LOCK_PREFIX}{room}")
        # TTL should be close to ROOM_LOCK_HOLD_TTL_SECONDS (600), not the
        # acquisition timeout (5). Allow a few seconds of slack.
        assert ttl > ROOM_LOCK_HOLD_TTL_SECONDS - 10
        assert ttl <= ROOM_LOCK_HOLD_TTL_SECONDS

        await rmc._release_room_lock(room, owner)

    async def test_second_acquire_blocks_until_first_releases(self, redis: RedisService):
        """Two RMC instances (simulating two workers) contend on same room."""
        rmc1 = _make_rmc(redis)
        rmc2 = _make_rmc(redis)
        room = "integration-contention"
        order = []

        async def worker(label: str, rmc: RoomMessageCenter):
            owner = await rmc._acquire_room_lock(room, timeout=10)
            assert owner is not None
            order.append(f"{label}-acquired")
            await asyncio.sleep(0.3)
            order.append(f"{label}-releasing")
            await rmc._release_room_lock(room, owner)

        # Start both workers; first one grabs lock, second polls until released
        await asyncio.gather(worker("W1", rmc1), worker("W2", rmc2))

        first = order[0].split("-")[0]
        second = "W2" if first == "W1" else "W1"
        assert order == [
            f"{first}-acquired",
            f"{first}-releasing",
            f"{second}-acquired",
            f"{second}-releasing",
        ]

    async def test_timeout_when_key_held_by_another(self, redis: RedisService):
        rmc = _make_rmc(redis)
        room = "integration-timeout"

        # Simulate another worker holding the lock
        await redis._client.set(f"{LOCK_PREFIX}{room}", "other-worker", ex=60)

        owner = await rmc._acquire_room_lock(room, timeout=1.5)
        assert owner is None

        await redis._client.delete(f"{LOCK_PREFIX}{room}")

    async def test_lock_survives_local_eviction(self, redis: RedisService):
        """After release evicts the local lock, re-acquiring works."""
        rmc = _make_rmc(redis)
        room = "integration-eviction"

        owner1 = await rmc._acquire_room_lock(room, timeout=5)
        await rmc._release_room_lock(room, owner1)
        assert room not in rmc._room_locks

        owner2 = await rmc._acquire_room_lock(room, timeout=5)
        assert owner2 is not None
        assert owner2 != owner1
        await rmc._release_room_lock(room, owner2)
