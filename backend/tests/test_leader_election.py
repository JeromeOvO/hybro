"""Tests for DAL Redis leader election primitives."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from dal.redis.lock import LeaderElectorImpl


def _make_client(*, set_return=True, eval_return=1) -> MagicMock:
    client = MagicMock()
    client.set = AsyncMock(return_value=set_return)
    client.eval = AsyncMock(return_value=eval_return)
    return client


@pytest.mark.asyncio
class TestLeaderAcquire:
    async def test_acquire_succeeds_first_time(self):
        leader = LeaderElectorImpl(
            _make_client(set_return=True), instance_id="inst-A"
        )

        assert await leader.try_acquire("job1", ttl_seconds=60) is True

    async def test_acquire_fails_when_held(self):
        leader = LeaderElectorImpl(
            _make_client(set_return=False), instance_id="inst-B"
        )

        assert await leader.try_acquire("job1", ttl_seconds=60) is False

    async def test_acquire_passes_correct_key_and_ttl_seconds(self):
        client = _make_client(set_return=True)
        leader = LeaderElectorImpl(client, instance_id="inst-A")

        await leader.try_acquire("stale_task_checker", ttl_seconds=120)

        client.set.assert_awaited_once_with(
            "leader:stale_task_checker", "inst-A", nx=True, ex=120
        )

    async def test_acquire_preserves_legacy_ttl_argument(self):
        client = _make_client(set_return=True)
        leader = LeaderElectorImpl(client, instance_id="inst-A")

        await leader.try_acquire("job1", ttl=30)

        client.set.assert_awaited_once_with(
            "leader:job1", "inst-A", nx=True, ex=30
        )


@pytest.mark.asyncio
class TestLeaderRenew:
    async def test_renew_succeeds_for_current_leader(self):
        leader = LeaderElectorImpl(_make_client(eval_return=1), instance_id="inst-A")

        assert await leader.renew("job1", ttl_seconds=60) is True

    async def test_renew_fails_for_non_leader(self):
        leader = LeaderElectorImpl(_make_client(eval_return=0), instance_id="inst-B")

        assert await leader.renew("job1", ttl_seconds=60) is False

    async def test_renew_passes_ttl_seconds_to_owner_checked_lua(self):
        client = _make_client(eval_return=1)
        leader = LeaderElectorImpl(client, instance_id="inst-A")

        await leader.renew("job1", ttl_seconds=75)

        args = client.eval.await_args.args
        assert args[1:] == (1, "leader:job1", "inst-A", "75")

    async def test_renew_preserves_legacy_ttl_argument(self):
        client = _make_client(eval_return=1)
        leader = LeaderElectorImpl(client, instance_id="inst-A")

        await leader.renew("job1", ttl=45)

        args = client.eval.await_args.args
        assert args[1:] == (1, "leader:job1", "inst-A", "45")


@pytest.mark.asyncio
class TestLeaderRelease:
    async def test_release_calls_owner_checked_lua(self):
        client = _make_client(eval_return=1)
        leader = LeaderElectorImpl(client, instance_id="inst-A")

        await leader.release("job1")

        args = client.eval.await_args.args
        assert args[1:] == (1, "leader:job1", "inst-A")

    async def test_release_noops_for_non_leader(self):
        leader = LeaderElectorImpl(_make_client(eval_return=0), instance_id="inst-B")

        await leader.release("job1")

    async def test_release_all_continues_on_error(self):
        client = _make_client()
        client.eval = AsyncMock(side_effect=[Exception("fail"), 1, 1])
        leader = LeaderElectorImpl(client, instance_id="inst-A")

        await leader.release_all(["job1", "job2", "job3"])

        assert client.eval.await_count == 3
