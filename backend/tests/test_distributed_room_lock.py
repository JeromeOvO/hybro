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
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from dal.redis.lock import RoomRedisDistributedLock
from execution.orchestration.room_message_center import (
    ROOM_LOCK_HOLD_TTL_SECONDS,
    RoomLockBackendUnavailable,
    RoomLockRenewalFailed,
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
        redis._client.set.assert_awaited_once_with(
            "room:lock:room-1", "owner-abc", nx=True, ex=60
        )

    @pytest.mark.asyncio
    async def test_acquire_records_attempt_start_as_conservative_lease_time(self):
        redis = _make_redis()
        original_sleep = asyncio.sleep

        async def delayed_success(*_args, **_kwargs):
            await original_sleep(0.01)
            return True

        redis._client.set = AsyncMock(side_effect=delayed_success)
        rmc = _make_rmc(redis=redis)
        before = time.monotonic()

        owner = await rmc._acquire_room_lock("room-1", timeout=5)
        after = time.monotonic()

        assert owner is not None
        acquired_at = rmc._room_lock_acquired_at_by_owner[owner]
        assert before <= acquired_at < after - 0.005
        await rmc._release_room_lock("room-1", owner)

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
        assert await redis.renew("room-1", "owner-abc", ttl=60) is None
        await redis.release("room-1", "owner-abc")

        redis._client.set.assert_not_awaited()
        redis._client.eval.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_acquire_returns_none_when_no_redis(self):
        rmc = _make_rmc(redis=None)
        result = await rmc._acquire_distributed_lock("room-1", "owner-abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_renew_distinguishes_backend_error_from_ownership_loss(self):
        redis = _make_redis()
        redis._client.eval = AsyncMock(
            side_effect=[ConnectionError("Redis unavailable"), 0, 1]
        )

        assert await redis.renew("room-1", "owner-abc", ttl=60) is None
        assert await redis.renew("room-1", "owner-abc", ttl=60) is False
        assert await redis.renew("room-1", "owner-abc", ttl=60) is True

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
        assert (
            "room-1" not in rmc._room_locks
            or not rmc._room_locks.get(
                "room-1", MagicMock(locked=lambda: False)
            ).locked()
        )

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

    @pytest.mark.asyncio
    async def test_cancellation_while_waiting_for_local_lock_releases_distributed(
        self,
    ):
        redis = _make_redis()
        rmc = _make_rmc(redis=redis)
        local_lock = rmc._get_local_lock("room-1")
        await local_lock.acquire()

        acquire_task = asyncio.create_task(rmc._acquire_room_lock("room-1", timeout=5))
        await asyncio.sleep(0)
        redis._client.set.assert_awaited_once()

        acquire_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await acquire_task

        redis._client.eval.assert_awaited_once()
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
            f"{first}-start",
            f"{first}-end",
            f"{second}-start",
            f"{second}-end",
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


class TestRoomLockRenewal:
    @pytest.mark.asyncio
    async def test_transient_backend_error_retries_until_confirmed_loss(
        self, monkeypatch
    ):
        lock = SimpleNamespace(
            renew=AsyncMock(side_effect=[None, True, False]),
        )
        rmc = _make_rmc(redis=lock)
        sleep = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep)

        with pytest.raises(RuntimeError, match="lost distributed room lock"):
            await rmc._renew_room_lock("room-1", "owner-abc")

        assert lock.renew.await_count == 3
        assert sleep.await_args_list[1].args == (1.0,)
        assert sleep.await_args_list[2].args == (ROOM_LOCK_HOLD_TTL_SECONDS / 3,)

    @pytest.mark.asyncio
    async def test_initial_renewal_delay_respects_time_since_acquire(self, monkeypatch):
        lock = SimpleNamespace(renew=AsyncMock(return_value=False))
        rmc = _make_rmc(redis=lock)
        sleep = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep)
        acquired_at = time.monotonic() - (ROOM_LOCK_HOLD_TTL_SECONDS - 50.0)

        with pytest.raises(RoomLockRenewalFailed, match="lost distributed room lock"):
            await rmc._renew_room_lock(
                "room-1",
                "owner-abc",
                acquired_at=acquired_at,
            )

        assert 0 <= sleep.await_args_list[0].args[0] <= 50.0

    @pytest.mark.asyncio
    async def test_backend_outage_fails_closed_at_lock_ttl(self, monkeypatch):
        lock = SimpleNamespace(renew=AsyncMock(return_value=None))
        rmc = _make_rmc(redis=lock)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        monkeypatch.setattr(
            "execution.orchestration.room_message_center.ROOM_LOCK_HOLD_TTL_SECONDS",
            0,
        )

        with pytest.raises(RoomLockRenewalFailed, match="TTL expired"):
            await rmc._renew_room_lock("room-1", "owner-abc")

        lock.renew.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hung_renewal_is_bounded_by_lock_ttl(self, monkeypatch):
        async def hang(*_args):
            await asyncio.Future()

        lock = SimpleNamespace(renew=AsyncMock(side_effect=hang))
        rmc = _make_rmc(redis=lock)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        monkeypatch.setattr(
            "execution.orchestration.room_message_center.ROOM_LOCK_HOLD_TTL_SECONDS",
            0.01,
        )

        with pytest.raises(RoomLockRenewalFailed, match="renewal timed out"):
            await rmc._renew_room_lock(
                "room-1",
                "owner-abc",
                acquired_at=time.monotonic(),
            )

        lock.renew.assert_awaited_once()


class TestRedisDisconnectionMidAcquisition:
    @pytest.mark.asyncio
    async def test_hung_redis_acquire_is_bounded_by_timeout(self):
        async def hang(*_args, **_kwargs):
            await asyncio.Future()

        redis = _make_redis(connected=True)
        redis._client.set = AsyncMock(side_effect=hang)
        rmc = _make_rmc(redis=redis)

        with pytest.raises(RoomLockBackendUnavailable, match="acquire timed out"):
            await rmc._acquire_room_lock("room-1", timeout=0.01)

        assert "room-1" not in rmc._room_locks

    @pytest.mark.asyncio
    async def test_delayed_redis_success_does_not_reset_local_acquire_timeout(self):
        original_sleep = asyncio.sleep

        async def delayed_success(*_args, **_kwargs):
            await original_sleep(0.08)
            return True

        redis = _make_redis(connected=True)
        redis._client.set = AsyncMock(side_effect=delayed_success)
        rmc = _make_rmc(redis=redis)
        local_lock = rmc._get_local_lock("room-1")
        await local_lock.acquire()
        started_at = time.monotonic()
        try:
            owner = await rmc._acquire_room_lock("room-1", timeout=0.1)
        finally:
            local_lock.release()

        assert owner is None
        assert time.monotonic() - started_at < 0.15
        redis._client.eval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_acquire_timeout_attempts_owner_safe_release_after_remote_mutation(
        self,
    ):
        mutation_applied = asyncio.Event()

        async def mutation_then_hang(*_args, **_kwargs):
            mutation_applied.set()
            await asyncio.Future()

        redis = _make_redis(connected=True)
        redis._client.set = AsyncMock(side_effect=mutation_then_hang)
        rmc = _make_rmc(redis=redis)

        with pytest.raises(RoomLockBackendUnavailable, match="acquire timed out"):
            await rmc._acquire_room_lock("room-1", timeout=0.01)

        assert mutation_applied.is_set()
        redis._client.eval.assert_awaited_once()
        assert redis._client.eval.await_args.args[3] != ""

    @pytest.mark.asyncio
    async def test_redis_errors_fail_closed(self):
        redis = _make_redis(connected=True)
        redis._client.set = AsyncMock(side_effect=ConnectionError("Redis unavailable"))
        rmc = _make_rmc(redis=redis)

        with pytest.raises(RoomLockBackendUnavailable):
            await rmc._acquire_room_lock("room-1", timeout=5)

        assert "room-1" not in rmc._room_locks

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
