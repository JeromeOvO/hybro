from __future__ import annotations

import json
from typing import Any

from common.utils.logger import get_logger

logger = get_logger(__name__)


class RelayStreamService:
    STREAM_PREFIX = "hub:relay:"
    HEARTBEAT_PREFIX = "hub:heartbeat:"

    def __init__(self, redis_service: Any, *, maxlen: int = 10_000, heartbeat_ttl: int = 90) -> None:
        self._redis = redis_service
        self._maxlen = maxlen
        self._heartbeat_ttl = heartbeat_ttl

    @property
    def is_connected(self) -> bool:
        return bool(getattr(self._redis, "is_connected", False))

    async def push_event(self, hub_id: str, event: dict) -> str | None:
        stream = f"{self.STREAM_PREFIX}{hub_id}"
        return await self._redis.xadd(
            stream, {"payload": json.dumps(event)}, maxlen=self._maxlen
        )

    async def read_events(
        self, hub_id: str, last_id: str = "0-0", count: int = 10, block_ms: int = 5000
    ) -> list[tuple[str, dict]]:
        result = await self._redis.xread(
            {f"{self.STREAM_PREFIX}{hub_id}": last_id},
            count=count,
            block=block_ms,
        )
        if not result:
            return []
        entries: list[tuple[str, dict]] = []
        for entry_id, data in result[0][1]:
            entries.append((entry_id, json.loads(data.get("payload", "{}"))))
        return entries

    async def record_heartbeat(self, hub_id: str) -> None:
        ok = await self._redis.set_with_ttl(
            f"{self.HEARTBEAT_PREFIX}{hub_id}", "1", ex=self._heartbeat_ttl
        )
        if not ok:
            logger.warning("Failed to record heartbeat for hub %s in Redis", hub_id)

    async def is_hub_alive(self, hub_id: str) -> bool:
        return await self._redis.exists(f"{self.HEARTBEAT_PREFIX}{hub_id}")


__all__ = ["RelayStreamService"]
