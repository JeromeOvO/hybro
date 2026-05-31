"""Leader election using Redis SETNX with Lua-based renewal and release.

LeaderElection provides distributed leader election for background jobs across
multiple instances. Uses SETNX for initial acquisition, Lua scripts for atomic
renewal/release, and a context manager with automatic renewal loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from common.utils.logger import get_logger
from infrastructure.redis_service import RedisService

logger = get_logger(__name__)

_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class LeaderElection:
    """Distributed leader election using Redis SETNX.

    Provides try_acquire/renew/release primitives and a context manager that
    automatically renews the lock. Safe to use across multiple instances.

    Example:
        from jobs.constants import STALE_TASK_CHECKER
        async with leader.hold(STALE_TASK_CHECKER, ttl_seconds=60):
            # Run job logic here
            # Lock is automatically renewed every 30 seconds
            pass
    """

    KEY_PREFIX = "leader:"

    class NotAcquiredError(Exception):
        """Raised when hold() context manager fails to acquire the lock."""
        pass

    def __init__(self, redis_service: RedisService, instance_id: str) -> None:
        """Initialize LeaderElection.

        Args:
            redis_service: RedisService instance for key-value operations
            instance_id: Unique identifier for this instance (e.g., container ID)
        """
        self._redis = redis_service
        self._instance_id = instance_id

    async def try_acquire(self, job_name: str, ttl_seconds: int = 60) -> bool:
        """Try to acquire leader lock for a job.

        Args:
            job_name: Name of the job (e.g., "stale_task_checker")
            ttl_seconds: Time-to-live for the lock in seconds

        Returns:
            True if lock was acquired, False if already held by another instance
        """
        key = f"{self.KEY_PREFIX}{job_name}"
        acquired = await self._redis.set_nx(key, self._instance_id, ex=ttl_seconds)
        if acquired:
            logger.debug("Acquired leader lock for %s", job_name)
        return acquired

    async def renew(self, job_name: str, ttl_seconds: int = 60) -> bool:
        """Renew leader lock if this instance currently holds it.

        Uses Lua script for atomic check-and-renew. Safe to call even if
        another instance has taken over.

        Args:
            job_name: Name of the job
            ttl_seconds: New time-to-live in seconds

        Returns:
            True if lock was renewed, False if not held by this instance
        """
        key = f"{self.KEY_PREFIX}{job_name}"
        result = await self._redis.eval_script(
            _RENEW_SCRIPT, 1, key, self._instance_id, str(ttl_seconds),
        )
        return result == 1

    async def release(self, job_name: str) -> None:
        """Release leader lock if this instance currently holds it.

        Uses Lua script for atomic check-and-release. Safe to call even if
        another instance has taken over (will be a no-op).

        Args:
            job_name: Name of the job
        """
        key = f"{self.KEY_PREFIX}{job_name}"
        await self._redis.eval_script(
            _RELEASE_SCRIPT, 1, key, self._instance_id,
        )
        logger.debug("Released leader lock for %s", job_name)

    async def release_all(self, job_names: list[str]) -> None:
        """Release multiple leader locks.

        Continues even if individual releases fail. Useful for cleanup on shutdown.

        Args:
            job_names: List of job names to release
        """
        for name in job_names:
            try:
                await self.release(name)
            except Exception as e:
                logger.warning("Failed to release leader lock %s: %s", name, e)

    @asynccontextmanager
    async def hold(
        self, job_name: str, ttl_seconds: int = 60, renew_interval: float | None = None,
    ) -> AsyncGenerator[None, None]:
        """Context manager that acquires, renews, and releases a leader lock.

        Automatically renews the lock at regular intervals. Ensures cleanup
        even on exceptions.

        Args:
            job_name: Name of the job
            ttl_seconds: Time-to-live for the lock in seconds
            renew_interval: How often to renew in seconds (defaults to ttl_seconds/2)

        Raises:
            NotAcquiredError: If lock could not be acquired

        Example:
            async with leader.hold("my_job", ttl_seconds=60):
                # Job logic here
                pass
        """
        if not await self.try_acquire(job_name, ttl_seconds):
            raise self.NotAcquiredError(f"Could not acquire lock for {job_name}")

        if renew_interval is None:
            renew_interval = ttl_seconds / 2

        async def _renew_loop():
            while True:
                await asyncio.sleep(renew_interval)
                if not await self.renew(job_name, ttl_seconds):
                    logger.warning("Lost leader lock for %s during renewal", job_name)
                    break

        renewal_task = asyncio.create_task(_renew_loop())
        try:
            yield
        finally:
            renewal_task.cancel()
            try:
                await renewal_task
            except asyncio.CancelledError:
                pass
            await self.release(job_name)
