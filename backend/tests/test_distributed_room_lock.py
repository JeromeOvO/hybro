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
from unittest.mock import AsyncMock, MagicMock

import pytest

from dal.redis.lock import RoomRedisDistributedLock
from execution.orchestration.room_message_center import (
    ROOM_LOCK_HOLD_TTL_SECONDS,
    RoomMessageCenter,
)


def _make_rmc(redis=None):
    """Build a RoomMessageCenter without triggering real service wiring."""
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc._room_locks = {}
    rmc._room_distributed_lock = redis
    return rmc


def _make_redis(*, set_nx_return=True, connected=True):
    """Build a DAL Redis room lock with a mocked Redis client."""
    client = MagicMock()
    client.set = AsyncMock(return_value=True if set_nx_return else None)
    client.eval = AsyncMock(return_value=1)
    return RoomRedisDistributedLock(client=client, enabled=connected)


# =========================================================================
# _acquire_distributed_lock / _release_distributed_lock
# =========================================================================

class TestDistributedLockPrimitives:
    @pytest.mark.asyncio
    async def test_acquire_calls_client_set_with_ttl(self):
        redis = _make_redis()
        rmc = _make_rmc(redis=redis)

        result = await rmc._acquire_distributed_lock("room-1", "owner-abc", ttl=60)

        assert result is True
        redis._client.set.assert_awaited_once_with("room:lock:room-1", "owner-abc", nx=True, ex=60)

    @pytest.mark.asyncio
    async def test_acquire_returns_false_when_key_exists(self):
        redis = _make_redis(set_nx_return=False)
        rmc = _make_rmc(redis=redis)

        result = await rmc._acquire_distributed_lock("room-1", "owner-abc")
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_returns_none_when_redis_disconnected(self):
        redis = _make_redis(connected=False)
        rmc = _make_rmc(redis=redis)

        result = await rmc._acquire_distributed_lock("room-1", "owner-abc")
        assert result is None
        redis._client.set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_room_lock_acquire_returns_none_and_release_noops(self):
        redis = _make_redis(connected=False)

        assert await redis.acquire("room-1", "owner-abc", ttl=60) is None
        await redis.release("room-1", "owner-abc")

        redis._client.set.assert_not_awaited()
        redis._client.eval.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_acquire_returns_none_when_no_redis(self):
        rmc = _make_rmc(redis=None)
        result = await rmc._acquire_distributed_lock("room-1", "owner-abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_release_calls_eval_script_with_lua(self):
        redis = _make_redis()
        rmc = _make_rmc(redis=redis)

        await rmc._release_distributed_lock("room-1", "owner-abc")

        redis._client.eval.assert_awaited_once()
        args = redis._client.eval.call_args
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
        redis._client.set.assert_awaited_once()
        _, kwargs = redis._client.set.call_args
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

        assert not rmc._room_locks["room-1"].locked()
        redis._client.eval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_release_without_owner_skips_distributed(self):
        """When owner is None (no-Redis path), only local lock is released."""
        rmc = _make_rmc(redis=None)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        await rmc._release_room_lock("room-1", owner)

        assert not rmc._room_locks["room-1"].locked()

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
        redis._client.eval.assert_awaited_once()  # distributed lock was released

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

        async def client_set_side_effect(key, value, nx=False, ex=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return True
            if call_count <= 3:
                return None  # key exists → contention
            return True

        redis._client.set = AsyncMock(side_effect=client_set_side_effect)
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
    async def test_falls_back_to_local_when_redis_errors_mid_poll(self):
        """If Redis starts erroring mid-acquisition, the lock degrades to
        local-only after 2 consecutive errors and still succeeds."""
        redis = _make_redis(connected=True)

        call_count = 0

        async def flaky_client_set(key, value, nx=False, ex=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # contended on first poll
            raise ConnectionError("Redis went away")

        redis._client.set = AsyncMock(side_effect=flaky_client_set)
        rmc = _make_rmc(redis=redis)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        # Should succeed via local-only fallback (not time out)
        assert owner is not None
        assert rmc._room_locks["room-1"].locked()
        await rmc._release_room_lock("room-1", owner)

    @pytest.mark.asyncio
    async def test_single_redis_error_retries_rather_than_falling_back(self):
        """One transient error followed by success stays on the distributed path."""
        redis = _make_redis(connected=True)

        call_count = 0

        async def transient_client_set(key, value, nx=False, ex=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("blip")
            return True  # succeeds on retry

        redis._client.set = AsyncMock(side_effect=transient_client_set)
        rmc = _make_rmc(redis=redis)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        assert owner is not None
        # Distributed lock was acquired (eval_script will be called on release)
        await rmc._release_room_lock("room-1", owner)
        redis._client.eval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_consecutive_errors_dont_trigger_fallback(self):
        """error → contention → error resets the counter, so no fallback."""
        redis = _make_redis(connected=True)

        call_count = 0

        async def interleaved_client_set(key, value, nx=False, ex=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("err1")
            if call_count == 2:
                return None  # contention (resets counter)
            if call_count == 3:
                raise ConnectionError("err2")
            return True  # succeeds

        redis._client.set = AsyncMock(side_effect=interleaved_client_set)
        rmc = _make_rmc(redis=redis)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        assert owner is not None
        await rmc._release_room_lock("room-1", owner)
        redis._client.eval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lock_reused_after_release(self):
        """Lock object stays in the dict after release and is reused."""
        rmc = _make_rmc(redis=None)

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        lock_obj = rmc._room_locks["room-1"]
        await rmc._release_room_lock("room-1", owner)
        assert "room-1" in rmc._room_locks
        assert rmc._room_locks["room-1"] is lock_obj

        owner2 = await rmc._acquire_room_lock("room-1", timeout=5)
        assert owner2 is not None
        assert rmc._room_locks["room-1"] is lock_obj
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
        with caplog.at_level(
            logging.WARNING, logger="execution.orchestration.room_message_center"
        ):
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
        with caplog.at_level(
            logging.INFO, logger="execution.orchestration.room_message_center"
        ):
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
        with caplog.at_level(
            logging.DEBUG, logger="execution.orchestration.room_message_center"
        ):
            await rmc._release_room_lock("room-1", owner, acquired_at=fake_acquired_at)

        assert not any("approaching TTL" in msg for msg in caplog.messages)
        assert not any("held lock for" in msg for msg in caplog.messages)
