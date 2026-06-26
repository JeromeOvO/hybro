from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

from common.config import settings
from common.errors import TransientError


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
        self._last_ping_ok = client is not None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._last_ping_ok

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

    def _transient(self, operation: str, exc: Exception) -> TransientError:
        return TransientError(
            f"Redis KV {operation} failed",
            details={"operation": operation, "error": str(exc)},
        )

    async def get(self, key: str) -> str | None:
        client = self._ensure_client()
        if client is None:
            return None
        try:
            return await client.get(key)
        except Exception as exc:
            raise self._transient("get", exc) from exc

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        client = self._ensure_client()
        if client is None:
            return None
        try:
            await client.set(key, value, ex=ttl)
        except Exception as exc:
            raise self._transient("set", exc) from exc
        return None

    async def delete(self, key: str) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            return bool(await client.delete(key))
        except Exception as exc:
            raise self._transient("delete", exc) from exc

    async def increment(self, key: str, amount: int = 1) -> int:
        client = self._ensure_client()
        if client is None:
            return 0
        try:
            return await client.incrby(key, amount)
        except Exception as exc:
            raise self._transient("increment", exc) from exc

    async def setnx(self, key: str, value: str, ttl: int) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            return bool(await client.set(key, value, nx=True, ex=ttl))
        except Exception as exc:
            raise self._transient("setnx", exc) from exc

    async def exists(self, key: str) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            return bool(await client.exists(key))
        except Exception as exc:
            raise self._transient("exists", exc) from exc

    async def ping(self) -> bool:
        client = self._ensure_client()
        if client is None:
            self._last_ping_ok = False
            return False
        try:
            await client.ping()
            self._last_ping_ok = True
            return True
        except Exception:
            self._last_ping_ok = False
            self._client = None
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
        self._client = None
        self._last_ping_ok = False
