"""Redis client for key-value and stream operations.

RedisService is a shared Redis client for key-value and stream operations.
It is SEPARATE from RedisBroker (in infrastructure/brokers/redis_broker.py)
which owns Pub/Sub. redis-py's PubSub monopolises its connection and cannot
be shared with key-value ops, so a dedicated client is required.

For blocking XREAD (hub relay), a second RedisService instance will be created
later so blocked stream reads don't starve key-value operations.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

from common.utils.logger import get_logger
from config.settings import settings

logger = get_logger(__name__)


class RedisService:
    """Redis client for key-value and stream operations.

    Separate from RedisBroker (Pub/Sub) because redis-py's PubSub monopolises
    its connection and cannot be shared with key-value operations.
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
            logger.info("RedisService connected to %s", self._url)
        except Exception as e:
            logger.warning("RedisService connection failed: %s — service disabled", e)
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
            logger.info("RedisService stopped")

    # --- Key-Value Operations ---

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
            logger.warning("RedisService set_nx failed for key %s: %s", key, e)
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
            logger.warning("RedisService get failed for key %s: %s", key, e)
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
            logger.warning("RedisService exists failed for key %s: %s", key, e)
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
            logger.warning("RedisService delete failed for key %s: %s", key, e)
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
            logger.warning("RedisService set_with_ttl failed for key %s: %s", key, e)
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
            logger.warning("RedisService eval_script failed: %s", e)
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
            logger.warning("RedisService xadd failed for stream %s: %s", stream, e)
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
            logger.warning("RedisService xread failed: %s", e)
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
            logger.warning("RedisService xlen failed for stream %s: %s", stream, e)
            return 0


def create_redis_service() -> RedisService | None:
    """Factory function to create RedisService from settings.

    Returns:
        RedisService instance if redis_url is configured, None otherwise
    """
    if not settings.redis_url:
        return None
    return RedisService(url=settings.redis_url)
