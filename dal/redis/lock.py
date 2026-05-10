from __future__ import annotations

import os
import socket
from typing import Any

import redis.asyncio as aioredis

from common.config import settings

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


class _RedisOwnerClient:
    def __init__(self, *, client: Any | None = None, url: str | None = None) -> None:
        self._client = client
        self._url = settings.redis_url if url is None else url

    def _ensure_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if not self._url:
            return None
        kwargs: dict[str, Any] = {"decode_responses": True}
        max_connections = getattr(settings, "redis_max_connections", None)
        if max_connections is not None:
            kwargs["max_connections"] = max_connections
        self._client = aioredis.from_url(self._url, **kwargs)
        return self._client

    async def _owner_eval(self, script: str, key: str, owner: str, *args: str) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            result = await client.eval(script, 1, key, owner, *args)
            return result == 1
        except Exception:
            return False


class DistributedLockImpl(_RedisOwnerClient):
    """Short-lived Redis lock with owner-checked release and renew."""

    async def acquire(self, key: str, owner: str, ttl: int = 60) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            return bool(await client.set(f"lock:{key}", owner, nx=True, ex=ttl))
        except Exception:
            return False

    async def release(self, key: str, owner: str) -> bool:
        return await self._owner_eval(_RELEASE_SCRIPT, f"lock:{key}", owner)

    async def renew(self, key: str, owner: str, ttl: int = 60) -> bool:
        return await self._owner_eval(_RENEW_SCRIPT, f"lock:{key}", owner, str(ttl))


class LeaderElectorImpl(_RedisOwnerClient):
    """Long-lived Redis leader election with owner-checked renewal."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        instance_id: str | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(client=client, url=url)
        self._instance_id = instance_id or f"{socket.gethostname()}:{os.getpid()}"

    async def try_acquire(self, job_name: str, ttl: int = 60) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            return bool(
                await client.set(
                    f"leader:{job_name}",
                    self._instance_id,
                    nx=True,
                    ex=ttl,
                )
            )
        except Exception:
            return False

    async def renew(self, job_name: str, ttl: int = 60) -> bool:
        return await self._owner_eval(
            _RENEW_SCRIPT,
            f"leader:{job_name}",
            self._instance_id,
            str(ttl),
        )

    async def release(self, job_name: str) -> None:
        await self._owner_eval(
            _RELEASE_SCRIPT,
            f"leader:{job_name}",
            self._instance_id,
        )

    async def release_all(self, job_names: list[str]) -> None:
        for job_name in job_names:
            await self.release(job_name)
