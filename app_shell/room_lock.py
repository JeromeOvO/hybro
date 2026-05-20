from __future__ import annotations

from typing import Any

from common.utils.logger import get_logger

logger = get_logger(__name__)


class RedisRoomDistributedLock:
    _ROOM_LOCK_PREFIX = "room:lock:"

    _RELEASE_LOCK_LUA = (
        "if redis.call('get',KEYS[1])==ARGV[1] then "
        "return redis.call('del',KEYS[1]) else return 0 end"
    )

    def __init__(self, redis_service: Any | None) -> None:
        self._redis_service = redis_service

    async def acquire(self, room_id: str, owner: str, ttl: int) -> bool | None:
        if self._redis_service is None or not self._redis_service.is_connected:
            return None
        client = getattr(self._redis_service, "_client", None)
        if client is None:
            return None
        try:
            result = await client.set(
                f"{self._ROOM_LOCK_PREFIX}{room_id}",
                owner,
                nx=True,
                ex=ttl,
            )
            return result is not None
        except Exception:
            logger.debug(
                "Redis error during distributed lock acquire for room %s",
                room_id,
                exc_info=True,
            )
            return None

    async def release(self, room_id: str, owner: str) -> None:
        if self._redis_service is None or not self._redis_service.is_connected:
            return
        try:
            await self._redis_service.eval_script(
                self._RELEASE_LOCK_LUA,
                1,
                f"{self._ROOM_LOCK_PREFIX}{room_id}",
                owner,
            )
        except Exception:
            logger.warning(
                "Redis error during distributed lock release for room %s (owner=%s); "
                "key will expire via TTL",
                room_id,
                owner[:8],
                exc_info=True,
            )


__all__ = ["RedisRoomDistributedLock"]
