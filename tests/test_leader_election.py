"""Tests for infrastructure.leader_election module.

Tests leader election primitives (acquire, renew, release) and the context
manager with automatic renewal.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from infrastructure.leader_election import LeaderElection


def _make_redis(set_nx_return=True, eval_return=1):
    """Helper to create a mock RedisService."""
    redis = MagicMock()
    redis.set_nx = AsyncMock(return_value=set_nx_return)
    redis.eval_script = AsyncMock(return_value=eval_return)
    return redis


@pytest.mark.asyncio
class TestLeaderAcquire:
    """Test leader lock acquisition."""

    async def test_acquire_succeeds_first_time(self):
        """Should acquire lock when not held by another instance."""
        leader = LeaderElection(_make_redis(set_nx_return=True), instance_id="inst-A")
        assert await leader.try_acquire("job1", ttl_seconds=60) is True

    async def test_acquire_fails_when_held(self):
        """Should fail to acquire lock when already held."""
        leader = LeaderElection(_make_redis(set_nx_return=False), instance_id="inst-B")
        assert await leader.try_acquire("job1", ttl_seconds=60) is False

    async def test_acquire_passes_correct_key_and_ttl(self):
        """Should pass correct Redis key and TTL to set_nx."""
        redis = _make_redis(set_nx_return=True)
        leader = LeaderElection(redis, instance_id="inst-A")
        await leader.try_acquire("stale_task_checker", ttl_seconds=120)
        redis.set_nx.assert_called_once_with("leader:stale_task_checker", "inst-A", ex=120)


@pytest.mark.asyncio
class TestLeaderRenew:
    """Test leader lock renewal."""

    async def test_renew_succeeds_for_current_leader(self):
        """Should renew lock when held by this instance."""
        leader = LeaderElection(_make_redis(eval_return=1), instance_id="inst-A")
        assert await leader.renew("job1", ttl_seconds=60) is True

    async def test_renew_fails_for_non_leader(self):
        """Should fail to renew lock when held by another instance."""
        leader = LeaderElection(_make_redis(eval_return=0), instance_id="inst-B")
        assert await leader.renew("job1", ttl_seconds=60) is False


@pytest.mark.asyncio
class TestLeaderRelease:
    """Test leader lock release."""

    async def test_release_calls_eval_script(self):
        """Should call eval_script with release script."""
        redis = _make_redis(eval_return=1)
        leader = LeaderElection(redis, instance_id="inst-A")
        await leader.release("job1")
        redis.eval_script.assert_called_once()

    async def test_release_noop_for_non_leader(self):
        """Should be a no-op when releasing lock held by another instance."""
        redis = _make_redis(eval_return=0)
        leader = LeaderElection(redis, instance_id="inst-B")
        await leader.release("job1")  # no error, no-op


@pytest.mark.asyncio
class TestLeaderReleaseAll:
    """Test releasing multiple leader locks."""

    async def test_release_all_calls_release_for_each(self):
        """Should release all locks in the list."""
        redis = _make_redis(eval_return=1)
        leader = LeaderElection(redis, instance_id="inst-A")
        await leader.release_all(["job1", "job2", "job3"])
        assert redis.eval_script.call_count == 3

    async def test_release_all_continues_on_error(self):
        """Should continue releasing even if one fails."""
        redis = _make_redis()
        redis.eval_script = AsyncMock(side_effect=[Exception("fail"), 1, 1])
        leader = LeaderElection(redis, instance_id="inst-A")
        await leader.release_all(["job1", "job2", "job3"])
        assert redis.eval_script.call_count == 3


@pytest.mark.asyncio
class TestLeaderHoldContext:
    """Test hold() context manager with automatic renewal."""

    async def test_hold_acquires_renews_releases(self):
        """Should acquire, renew, and release lock in context manager."""
        redis = _make_redis(set_nx_return=True, eval_return=1)
        leader = LeaderElection(redis, instance_id="inst-A")
        async with leader.hold("job1", ttl_seconds=10, renew_interval=0.1):
            await asyncio.sleep(0.25)  # allow ~2 renewals
        # Should have called: try_acquire (set_nx), renew+release (eval_script)
        assert redis.set_nx.call_count == 1
        assert redis.eval_script.call_count >= 2  # at least 1 renew + 1 release

    async def test_hold_raises_when_not_acquired(self):
        """Should raise NotAcquiredError when lock cannot be acquired."""
        redis = _make_redis(set_nx_return=False)
        leader = LeaderElection(redis, instance_id="inst-A")
        with pytest.raises(LeaderElection.NotAcquiredError):
            async with leader.hold("job1", ttl_seconds=10):
                pass

    async def test_hold_releases_on_exception(self):
        """Should release lock even when exception occurs in context."""
        redis = _make_redis(set_nx_return=True, eval_return=1)
        leader = LeaderElection(redis, instance_id="inst-A")
        with pytest.raises(ValueError):
            async with leader.hold("job1", ttl_seconds=10, renew_interval=100):
                raise ValueError("test error")
        # Release should have been called even on exception
        assert redis.eval_script.call_count >= 1
