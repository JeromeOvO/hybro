from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

from common.config import settings


class RedisKVImpl:
    """Redis key-value DAL backed directly by redis.asyncio."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        url: str | None = None,
    ) -> None:
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

    async def get(self, key: str) -> str | None:
        client = self._ensure_client()
        if client is None:
            return None
        try:
            return await client.get(key)
        except Exception:
            return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        client = self._ensure_client()
        if client is None:
            return None
        try:
            await client.set(key, value, ex=ttl)
        except Exception:
            return None
        return None

    async def delete(self, key: str) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            return bool(await client.delete(key))
        except Exception:
            return False

    async def increment(self, key: str, amount: int = 1) -> int:
        client = self._ensure_client()
        if client is None:
            return 0
        try:
            return await client.incrby(key, amount)
        except Exception:
            return 0

    async def setnx(self, key: str, value: str, ttl: int) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            return bool(await client.set(key, value, nx=True, ex=ttl))
        except Exception:
            return False

    async def exists(self, key: str) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            return bool(await client.exists(key))
        except Exception:
            return False

    async def ping(self) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            await client.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
        self._client = None
