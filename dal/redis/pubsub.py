from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from common.config import settings
from common.errors import TransientError


class RedisPubSubImpl:
    """Redis Pub/Sub DAL backed by a dedicated redis.asyncio connection."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        url: str | None = None,
        max_connections: int | None = None,
    ) -> None:
        self._client = client
        self._url = settings.redis_url if url is None else url
        self.max_connections = (
            getattr(settings, "redis_max_connections", None)
            if max_connections is None
            else max_connections
        )

    def _ensure_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if not self._url:
            return None
        kwargs: dict[str, Any] = {"decode_responses": True, "retry_on_timeout": True}
        if self.max_connections is not None:
            kwargs["max_connections"] = self.max_connections
        self._client = aioredis.from_url(self._url, **kwargs)
        return self._client

    def _transient(self, operation: str, exc: Exception) -> TransientError:
        return TransientError(
            f"Redis Pub/Sub {operation} failed",
            details={"operation": operation, "error": str(exc)},
        )

    async def publish(self, channel: str, message: str) -> None:
        client = self._ensure_client()
        if client is None:
            return None
        try:
            await client.publish(channel, message)
        except Exception as exc:
            raise self._transient("publish", exc) from exc
        return None

    async def subscribe(self, channel: str) -> AsyncIterator[str]:
        client = self._ensure_client()
        if client is None:
            return _empty_iterator()
        return _message_iterator(client, channel, self._transient)

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


async def _message_iterator(
    client: Any,
    channel: str,
    transient_factory,
) -> AsyncIterator[str]:
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if data is not None:
                yield data
    except Exception as exc:
        raise transient_factory("subscribe", exc) from exc
    finally:
        try:
            await pubsub.unsubscribe(channel)
        finally:
            await pubsub.aclose()
