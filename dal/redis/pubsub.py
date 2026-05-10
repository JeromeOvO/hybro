from __future__ import annotations

from typing import Any, AsyncIterator

import redis.asyncio as aioredis

from common.config import settings


class RedisPubSubImpl:
    """Redis Pub/Sub DAL backed by a dedicated redis.asyncio connection."""

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
        kwargs: dict[str, Any] = {"decode_responses": True, "retry_on_timeout": True}
        max_connections = getattr(settings, "redis_max_connections", None)
        if max_connections is not None:
            kwargs["max_connections"] = max_connections
        self._client = aioredis.from_url(self._url, **kwargs)
        return self._client

    async def publish(self, channel: str, message: str) -> None:
        client = self._ensure_client()
        if client is None:
            return None
        try:
            await client.publish(channel, message)
        except Exception:
            return None
        return None

    async def subscribe(self, channel: str) -> AsyncIterator[str]:
        client = self._ensure_client()
        if client is None:
            return _empty_iterator()
        pubsub = client.pubsub()
        return _message_iterator(pubsub, channel)

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


async def _empty_iterator() -> AsyncIterator[str]:
    if False:
        yield ""


async def _message_iterator(pubsub: Any, channel: str) -> AsyncIterator[str]:
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            yield message.get("data")
    finally:
        try:
            await pubsub.unsubscribe(channel)
        finally:
            await pubsub.aclose()
