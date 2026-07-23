from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

from common.config import settings
from common.errors import TransientError


class RedisStreamsImpl:
    """Redis Streams DAL backed by a dedicated redis.asyncio connection."""

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
        kwargs: dict[str, Any] = {
            "decode_responses": True,
            "socket_connect_timeout": 5,
        }
        max_connections = getattr(settings, "redis_max_connections", None)
        if max_connections is not None:
            kwargs["max_connections"] = max_connections
        self._client = aioredis.from_url(self._url, **kwargs)
        return self._client

    def _transient(self, operation: str, exc: Exception) -> TransientError:
        return TransientError(
            f"Redis Streams {operation} failed",
            details={"operation": operation, "error": str(exc)},
        )

    async def xadd(self, stream: str, fields: dict, maxlen: int | None = None) -> str:
        client = self._ensure_client()
        if client is None:
            return ""
        try:
            return str(await client.xadd(stream, fields, maxlen=maxlen))
        except Exception as exc:
            raise self._transient("xadd", exc) from exc

    async def xread(
        self, streams: dict, block: int = 0, count: int = 100
    ) -> list[dict]:
        client = self._ensure_client()
        if client is None:
            return []
        try:
            response = await client.xread(streams, block=block, count=count)
        except Exception as exc:
            raise self._transient("xread", exc) from exc

        entries: list[dict] = []
        for stream_name, stream_entries in response or []:
            for entry_id, fields in stream_entries:
                entries.append(
                    {"stream": stream_name, "id": entry_id, "fields": fields}
                )
        return entries

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
