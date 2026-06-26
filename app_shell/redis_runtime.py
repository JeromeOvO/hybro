"""App-shell Redis runtime for key-value, stream, and leader-election operations.

The command client is separate from delivery-owned Pub/Sub/KV clients. A second
streams client can be created for blocking XREAD so stream reads do not starve
key-value operations.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis

from common.config.settings import settings
from common.utils.logger import get_logger
from hub_runtime_bridge.transport.relay_streams import RelayStreamService

logger = get_logger(__name__)


class AppShellRedisService:
    """Redis client for key-value and stream operations.

    Separate from delivery Pub/Sub because redis-py's PubSub monopolises its
    connection and cannot be shared with key-value operations.
    """

    def __init__(self, url: str):
        """Initialize RedisService with connection URL.

        Args:
            url: Redis connection URL (e.g., "redis://localhost:6379/0")
        """
        self._url = url
        self._client: aioredis.Redis | None = None

    @property
    def is_connected(self) -> bool:
        """Whether the client was initialized successfully.

        Note: This checks initialization, not live connectivity. If Redis
        goes down after start(), this remains True until stop() is called.
        Per-method try/except handles actual connection failures gracefully.
        """
        return self._client is not None

    async def start(self) -> None:
        """Connect to Redis and verify connectivity.

        Creates client with decode_responses=True, pings to verify connection.
        On error, sets _client=None and warns (graceful degradation).
        """
        if self._client is not None:
            return  # idempotent
        try:
            self._client = aioredis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=5,
                max_connections=settings.redis_max_connections,
            )
            await self._client.ping()
            logger.info("AppShellRedisService connected to %s", self._url)
        except Exception as e:
            logger.warning(
                "AppShellRedisService connection failed: %s — service disabled", e
            )
            self._client = None

    async def stop(self) -> None:
        """Close Redis connection.

        Idempotent. Safe to call even if start() was never called.
        """
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
            logger.info("AppShellRedisService stopped")

    # --- Key-Value Operations ---

    async def incr(self, key: str) -> int:
        """Increment key by 1 (INCR). Creates key with value 1 if it doesn't exist.

        Args:
            key: Redis key

        Returns:
            The value after incrementing, or 0 on error
        """
        if not self.is_connected:
            return 0
        try:
            return await self._client.incr(key)
        except Exception as e:
            logger.warning("AppShellRedisService incr failed for key %s: %s", key, e)
            return 0

    async def set_nx(self, key: str, value: str, ex: int | None = None) -> bool:
        """Set key to value if key does not exist (SET NX).

        Args:
            key: Redis key
            value: String value
            ex: Optional expiry time in seconds

        Returns:
            True if key was set, False if key already existed
        """
        if not self.is_connected:
            return False
        try:
            result = await self._client.set(key, value, nx=True, ex=ex)
            return result is not None
        except Exception as e:
            logger.warning("AppShellRedisService set_nx failed for key %s: %s", key, e)
            return False

    async def get(self, key: str) -> str | None:
        """Get value for key.

        Args:
            key: Redis key

        Returns:
            String value or None if key doesn't exist
        """
        if not self.is_connected:
            return None
        try:
            return await self._client.get(key)
        except Exception as e:
            logger.warning("AppShellRedisService get failed for key %s: %s", key, e)
            return None

    async def exists(self, key: str) -> bool:
        """Check if key exists.

        Args:
            key: Redis key

        Returns:
            True if key exists, False otherwise
        """
        if not self.is_connected:
            return False
        try:
            result = await self._client.exists(key)
            return result > 0
        except Exception as e:
            logger.warning("AppShellRedisService exists failed for key %s: %s", key, e)
            return False

    async def delete(self, key: str) -> bool:
        """Delete key.

        Args:
            key: Redis key

        Returns:
            True if key was deleted, False otherwise
        """
        if not self.is_connected:
            return False
        try:
            result = await self._client.delete(key)
            return result > 0
        except Exception as e:
            logger.warning("AppShellRedisService delete failed for key %s: %s", key, e)
            return False

    async def set_with_ttl(
        self, key: str, value: str, ex: int | None = None
    ) -> bool:
        """Set key to value with optional TTL.

        Args:
            key: Redis key
            value: String value
            ex: Optional expiry time in seconds

        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected:
            return False
        try:
            await self._client.set(key, value, ex=ex)
            return True
        except Exception as e:
            logger.warning(
                "AppShellRedisService set_with_ttl failed for key %s: %s", key, e
            )
            return False

    # --- Lua Script Evaluation ---

    async def eval_script(
        self, script: str, num_keys: int, *args: str
    ) -> Any:
        """Evaluate a Lua script.

        Args:
            script: Lua script string
            num_keys: Number of keys in args
            *args: Keys followed by arguments

        Returns:
            Script result or None on error
        """
        if not self.is_connected:
            return None
        try:
            return await self._client.eval(script, num_keys, *args)
        except Exception as e:
            logger.warning("AppShellRedisService eval_script failed: %s", e)
            return None

    # --- Redis Streams Operations ---

    async def xadd(
        self,
        stream: str,
        fields: dict[str, str],
        maxlen: int | None = None,
    ) -> str | None:
        """Add entry to a stream.

        Args:
            stream: Stream name
            fields: Field-value pairs to add
            maxlen: Optional max stream length (approximate trimming)

        Returns:
            Entry ID or None on error
        """
        if not self.is_connected:
            return None
        try:
            kwargs: dict[str, Any] = {}
            if maxlen is not None:
                kwargs["maxlen"] = maxlen
                kwargs["approximate"] = True
            return await self._client.xadd(stream, fields, **kwargs)
        except Exception as e:
            logger.warning(
                "AppShellRedisService xadd failed for stream %s: %s", stream, e
            )
            return None

    async def xread(
        self,
        streams: dict[str, str],
        count: int = 10,
        block: int = 5000,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]] | None:
        """Read entries from one or more streams.

        Args:
            streams: Dict mapping stream names to IDs (e.g., {"stream": "0"})
            count: Max number of entries per stream
            block: Block for this many milliseconds (0 = forever)

        Returns:
            List of (stream_name, [(entry_id, fields)]) tuples or None on error
        """
        if not self.is_connected:
            return None
        try:
            result = await self._client.xread(streams, count=count, block=block)
            return result if result else []
        except Exception as e:
            logger.warning("AppShellRedisService xread failed: %s", e)
            return None

    async def xlen(self, stream: str) -> int:
        """Get length of a stream.

        Args:
            stream: Stream name

        Returns:
            Number of entries in stream or 0 on error
        """
        if not self.is_connected:
            return 0
        try:
            return await self._client.xlen(stream)
        except Exception as e:
            logger.warning(
                "AppShellRedisService xlen failed for stream %s: %s", stream, e
            )
            return 0


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


class AppShellLeaderElection:
    """Distributed leader election using Redis SETNX."""

    KEY_PREFIX = "leader:"

    class NotAcquiredError(Exception):
        """Raised when hold() context manager fails to acquire the lock."""

        pass

    def __init__(self, redis_service: AppShellRedisService, instance_id: str) -> None:
        self._redis = redis_service
        self._instance_id = instance_id

    async def try_acquire(self, job_name: str, ttl_seconds: int = 60) -> bool:
        key = f"{self.KEY_PREFIX}{job_name}"
        acquired = await self._redis.set_nx(key, self._instance_id, ex=ttl_seconds)
        if acquired:
            logger.debug("Acquired leader lock for %s", job_name)
        return acquired

    async def renew(self, job_name: str, ttl_seconds: int = 60) -> bool:
        key = f"{self.KEY_PREFIX}{job_name}"
        result = await self._redis.eval_script(
            _RENEW_SCRIPT,
            1,
            key,
            self._instance_id,
            str(ttl_seconds),
        )
        return result == 1

    async def release(self, job_name: str) -> None:
        key = f"{self.KEY_PREFIX}{job_name}"
        await self._redis.eval_script(
            _RELEASE_SCRIPT,
            1,
            key,
            self._instance_id,
        )
        logger.debug("Released leader lock for %s", job_name)

    async def release_all(self, job_names: list[str]) -> None:
        for name in job_names:
            try:
                await self.release(name)
            except Exception as e:
                logger.warning("Failed to release leader lock %s: %s", name, e)

    @asynccontextmanager
    async def hold(
        self,
        job_name: str,
        ttl_seconds: int = 60,
        renew_interval: float | None = None,
    ) -> AsyncGenerator[None, None]:
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


class _AppShellRedisKVBridge:
    """Adapt app-shell Redis commands to the DAL KV surface used by relay streams."""

    def __init__(self, redis_service: AppShellRedisService) -> None:
        self._redis = redis_service

    @property
    def is_connected(self) -> bool:
        return self._redis.is_connected

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        await self._redis.set_with_ttl(key, value, ex=ttl)

    async def exists(self, key: str) -> bool:
        return await self._redis.exists(key)


class AppShellRelayStreamService(RelayStreamService):
    """App-shell name for active hub relay stream behavior."""

    def __init__(
        self,
        streams_client: AppShellRedisService,
        command_client: AppShellRedisService | None = None,
        *,
        maxlen: int = 10_000,
        heartbeat_ttl: int = 90,
    ) -> None:
        kv_client = command_client if command_client is not None else streams_client
        kv = _AppShellRedisKVBridge(kv_client)
        super().__init__(
            streams_client,
            kv=kv,
            maxlen=maxlen,
            heartbeat_ttl=heartbeat_ttl,
        )


@dataclass
class AppShellRedisRuntime:
    command_client: AppShellRedisService | None
    streams_client: AppShellRedisService | None
    leader: AppShellLeaderElection | None
    relay_streams: AppShellRelayStreamService | None


def _create_client() -> AppShellRedisService | None:
    if not settings.redis_url:
        return None
    return AppShellRedisService(url=settings.redis_url)


def create_app_shell_redis_runtime(*, instance_id: str | None = None) -> AppShellRedisRuntime:
    """Create app-shell Redis clients and optional leader/stream helpers.

    Returns:
        Runtime bundle with no clients when redis_url is not configured.
    """
    command_client = _create_client()
    streams_client = _create_client()
    leader = (
        AppShellLeaderElection(command_client, instance_id)
        if command_client is not None and instance_id is not None
        else None
    )
    relay_streams = (
        AppShellRelayStreamService(
            streams_client,
            command_client,
            maxlen=settings.relay_stream_maxlen,
            heartbeat_ttl=settings.relay_hub_heartbeat_ttl,
        )
        if streams_client is not None
        else None
    )
    return AppShellRedisRuntime(
        command_client=command_client,
        streams_client=streams_client,
        leader=leader,
        relay_streams=relay_streams,
    )


__all__ = [
    "AppShellLeaderElection",
    "AppShellRedisRuntime",
    "AppShellRedisService",
    "AppShellRelayStreamService",
    "create_app_shell_redis_runtime",
]
