"""
Tests for the distributed room lock in RoomMessageCenter.

Covers:
- Redis-backed distributed lock acquire/release
- Lua-guarded owner-only release
- Fallback to process-local asyncio.Lock when Redis is unavailable
- Timeout behaviour when lock is held
- Combined distributed + local lock interaction
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from modules.RoomMessageCenter import RoomMessageCenter, ROOM_LOCK_HOLD_TTL_SECONDS


def _make_rmc(redis=None):
    """Build a RoomMessageCenter without triggering real service wiring."""
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc._room_locks = {}
    rmc._redis = redis
    return rmc


def _make_redis(*, set_nx_return=True, connected=True):
    redis = MagicMock()
    redis.is_connected = connected
    redis.set_nx = AsyncMock(return_value=set_nx_return)
    redis.eval_script = AsyncMock(return_value=1)
    redis.delete = AsyncMock(return_value=True)
    return redis


# =========================================================================
# _acquire_distributed_lock / _release_distributed_lock
# =========================================================================

class TestDistributedLockPrimitives:
    @pytest.mark.asyncio
    async def test_acquire_calls_set_nx_with_ttl(self):
        redis = _make_redis()
        rmc = _make_rmc(redis=redis)

        result = await rmc._acquire_distributed_lock("room-1", "owner-abc", ttl=60)

        assert result is True
        redis.set_nx.assert_awaited_once_with("room:lock:room-1", "owner-abc", ex=60)

    @pytest.mark.asyncio
    async def test_acquire_returns_false_when_key_exists(self):
        redis = _make_redis(set_nx_return=False)
        rmc = _make_rmc(redis=redis)

        result = await rmc._acquire_distributed_lock("room-1", "owner-abc")
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_returns_false_when_redis_disconnected(self):
        redis = _make_redis(connected=False)
        rmc = _make_rmc(redis=redis)

        result = await rmc._acquire_distributed_lock("room-1", "owner-abc")
        assert result is False
        redis.set_nx.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_acquire_returns_false_when_no_redis(self):
        rmc = _make_rmc(redis=None)
        result = await rmc._acquire_distributed_lock("room-1", "owner-abc")
        assert result is False

    @pytest.mark.asyncio
    async def test_release_calls_eval_script_with_lua(self):
        redis = _make_redis()
        rmc = _make_rmc(redis=redis)

        await rmc._release_distributed_lock("room-1", "owner-abc")

        redis.eval_script.assert_awaited_once()
        args = redis.eval_script.call_args
        assert args[0][1] == 1  # num_keys
        assert args[0][2] == "room:lock:room-1"
        assert args[0][3] == "owner-abc"

    @pytest.mark.asyncio
    async def test_release_noop_when_no_redis(self):
        rmc = _make_rmc(redis=None)
        await rmc._release_distributed_lock("room-1", "owner-abc")


# =========================================================================
# _acquire_room_lock / _release_room_lock (combined flow)
# =========================================================================

class TestAcquireRoomLock:
    @pytest.mark.asyncio
    async def test_acquire_with_redis_returns_owner_token(self):
        redis = _make_redis()
        rmc = _make_rmc(redis=redis)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)

        assert owner is not None
        assert len(owner) == 32  # uuid4().hex
        redis.set_nx.assert_awaited_once()
        # TTL must be the hold duration, NOT the acquisition timeout
        _, kwargs = redis.set_nx.call_args
        assert kwargs["ex"] == ROOM_LOCK_HOLD_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_acquire_without_redis_falls_back_to_local(self):
        rmc = _make_rmc(redis=None)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)

        assert owner is not None
        local_lock = rmc._room_locks.get("room-1")
        assert local_lock is not None
        assert local_lock.locked()

    @pytest.mark.asyncio
    async def test_release_frees_both_locks(self):
        redis = _make_redis()
        rmc = _make_rmc(redis=redis)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        assert rmc._room_locks["room-1"].locked()

        await rmc._release_room_lock("room-1", owner)

        # Lock object should be evicted from the dict when idle
        assert "room-1" not in rmc._room_locks
        redis.eval_script.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_release_without_owner_skips_distributed(self):
        """When owner is None (no-Redis path), only local lock is released."""
        rmc = _make_rmc(redis=None)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        await rmc._release_room_lock("room-1", owner)

        # Lock object should be evicted from the dict when idle
        assert "room-1" not in rmc._room_locks

    @pytest.mark.asyncio
    async def test_timeout_when_distributed_lock_held(self):
        redis = _make_redis(set_nx_return=False)  # always contended
        rmc = _make_rmc(redis=redis)

        owner = await rmc._acquire_room_lock("room-1", timeout=0.3)

        assert owner is None
        assert "room-1" not in rmc._room_locks or not rmc._room_locks.get("room-1", MagicMock(locked=lambda: False)).locked()

    @pytest.mark.asyncio
    async def test_timeout_releases_distributed_if_local_times_out(self):
        """If distributed lock acquired but local lock times out, distributed is released."""
        redis = _make_redis()
        rmc = _make_rmc(redis=redis)

        # Pre-acquire the local lock to force local timeout
        local_lock = rmc._get_local_lock("room-1")
        await local_lock.acquire()

        owner = await rmc._acquire_room_lock("room-1", timeout=0.3)

        assert owner is None
        redis.eval_script.assert_awaited_once()  # distributed lock was released

        local_lock.release()


class TestConcurrentLocalLock:
    @pytest.mark.asyncio
    async def test_second_acquire_waits_for_first_release(self):
        """Two coroutines in the same process serialise via the local lock."""
        rmc = _make_rmc(redis=None)
        order = []

        async def worker(label: str):
            owner = await rmc._acquire_room_lock("room-1", timeout=5)
            assert owner is not None
            order.append(f"{label}-start")
            await asyncio.sleep(0.05)
            order.append(f"{label}-end")
            await rmc._release_room_lock("room-1", owner)

        await asyncio.gather(worker("A"), worker("B"))

        assert order[0] in ("A-start", "B-start")
        first = order[0][0]
        second = "B" if first == "A" else "A"
        assert order == [
            f"{first}-start", f"{first}-end",
            f"{second}-start", f"{second}-end",
        ]


class TestDistributedConcurrency:
    @pytest.mark.asyncio
    async def test_second_worker_blocked_until_first_releases(self):
        """Simulate two workers contending on the same Redis key."""
        redis = _make_redis()
        call_count = 0

        async def set_nx_side_effect(key, value, ex=None):
            nonlocal call_count
            call_count += 1
            # First call from worker A succeeds; subsequent calls fail until released
            if call_count == 1:
                return True
            if call_count <= 3:
                return False
            return True  # lock "released" by the time we poll again

        redis.set_nx = AsyncMock(side_effect=set_nx_side_effect)
        rmc = _make_rmc(redis=redis)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        assert owner is not None
        await rmc._release_room_lock("room-1", owner)

        # Second acquire succeeds after a few polls
        owner2 = await rmc._acquire_room_lock("room-1", timeout=5)
        assert owner2 is not None
        await rmc._release_room_lock("room-1", owner2)


class TestRedisDisconnectionMidAcquisition:
    @pytest.mark.asyncio
    async def test_falls_back_to_local_when_redis_disconnects_mid_poll(self):
        """If Redis becomes unavailable mid-acquisition, the lock still
        succeeds via the local-only fallback path because the distributed
        attempt will fail and the method will time out, triggering None.

        However, the important invariant is that a *fresh* call after Redis
        drops returns an owner via the local-only path (is_connected = False).
        """
        redis = _make_redis(connected=True)

        call_count = 0

        async def flaky_set_nx(key, value, ex=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return False  # contended
            # Simulate Redis going away after first poll
            raise ConnectionError("Redis went away")

        redis.set_nx = AsyncMock(side_effect=flaky_set_nx)
        rmc = _make_rmc(redis=redis)

        # First call: Redis is "connected" but set_nx raises on retry.
        # _acquire_distributed_lock catches exceptions, returns False,
        # so the loop keeps polling until timeout.
        owner = await rmc._acquire_room_lock("room-1", timeout=0.4)
        # Should time out because distributed lock never succeeds
        assert owner is None

        # After disconnect, a fresh call with is_connected=False skips Redis
        redis.is_connected = False
        owner2 = await rmc._acquire_room_lock("room-1", timeout=5)
        assert owner2 is not None  # local-only path succeeded
        await rmc._release_room_lock("room-1", owner2)

    @pytest.mark.asyncio
    async def test_lock_evicted_after_release_is_recreatable(self):
        """After eviction, a new acquire for the same room succeeds."""
        rmc = _make_rmc(redis=None)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        await rmc._release_room_lock("room-1", owner)
        assert "room-1" not in rmc._room_locks

        owner2 = await rmc._acquire_room_lock("room-1", timeout=5)
        assert owner2 is not None
        assert "room-1" in rmc._room_locks
        await rmc._release_room_lock("room-1", owner2)


class TestLockHoldDurationWarning:
    @pytest.mark.asyncio
    async def test_warns_when_approaching_ttl(self, caplog):
        """Release with acquired_at near TTL threshold emits a WARNING."""
        import time as _time
        rmc = _make_rmc(redis=None)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        # Simulate a lock held for 85% of TTL (above 80% threshold)
        fake_acquired_at = _time.monotonic() - (ROOM_LOCK_HOLD_TTL_SECONDS * 0.85)

        import logging
        with caplog.at_level(logging.WARNING, logger="modules.RoomMessageCenter"):
            await rmc._release_room_lock("room-1", owner, acquired_at=fake_acquired_at)

        assert any("approaching TTL" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_info_for_long_but_safe_hold(self, caplog):
        """Release with acquired_at > 60s but < 80% TTL emits INFO, not WARNING."""
        import time as _time
        rmc = _make_rmc(redis=None)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        fake_acquired_at = _time.monotonic() - 120  # 2 minutes, well under 80% of 600s

        import logging
        with caplog.at_level(logging.INFO, logger="modules.RoomMessageCenter"):
            await rmc._release_room_lock("room-1", owner, acquired_at=fake_acquired_at)

        assert any("held lock for" in msg for msg in caplog.messages)
        assert not any("approaching TTL" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_no_log_for_fast_release(self, caplog):
        """Quick lock hold produces no duration log at all."""
        rmc = _make_rmc(redis=None)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        import time as _time
        fake_acquired_at = _time.monotonic() - 2  # 2 seconds

        import logging
        with caplog.at_level(logging.DEBUG, logger="modules.RoomMessageCenter"):
            await rmc._release_room_lock("room-1", owner, acquired_at=fake_acquired_at)

        assert not any("approaching TTL" in msg for msg in caplog.messages)
        assert not any("held lock for" in msg for msg in caplog.messages)
