from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

from common.config import settings


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

    async def xadd(
        self, stream: str, fields: dict, maxlen: int | None = None
    ) -> str:
        client = self._ensure_client()
        if client is None:
            return ""
        try:
            return str(await client.xadd(stream, fields, maxlen=maxlen))
        except Exception:
            return ""

    async def xread(
        self, streams: dict, block: int = 0, count: int = 100
    ) -> list[dict]:
        client = self._ensure_client()
        if client is None:
            return []
        try:
            response = await client.xread(streams, block=block, count=count)
        except Exception:
            return []

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
