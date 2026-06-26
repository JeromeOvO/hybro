from __future__ import annotations

import json
from typing import Any

from common.protocols import RedisKV, RedisStreams
from common.utils.logger import get_logger

logger = get_logger(__name__)


class RelayStreamService:
    STREAM_PREFIX = "hub:relay:"
    HEARTBEAT_PREFIX = "hub:heartbeat:"

    def __init__(
        self,
        streams: RedisStreams,
        *,
        kv: RedisKV | None = None,
        maxlen: int = 10_000,
        heartbeat_ttl: int = 90,
    ) -> None:
        self._streams = streams
        self._kv = kv
        self._maxlen = maxlen
        self._heartbeat_ttl = heartbeat_ttl

    @property
    def is_connected(self) -> bool:
        streams_connected = bool(getattr(self._streams, "is_connected", False))
        kv_connected = (
            True if self._kv is None else bool(getattr(self._kv, "is_connected", False))
        )
        return streams_connected and kv_connected

    async def push_event(self, hub_id: str, event: dict) -> str | None:
        stream = f"{self.STREAM_PREFIX}{hub_id}"
        try:
            entry_id = await self._streams.xadd(
                stream,
                {"payload": json.dumps(event)},
                maxlen=self._maxlen,
            )
        except Exception:
            logger.warning(
                "Failed to push relay event for hub %s to Redis stream",
                hub_id,
                exc_info=True,
            )
            return None
        return entry_id or None

    async def read_events(
        self, hub_id: str, last_id: str = "0-0", count: int = 10, block_ms: int = 5000
    ) -> list[tuple[str, dict]]:
        try:
            rows = await self._streams.xread(
                {f"{self.STREAM_PREFIX}{hub_id}": last_id},
                count=count,
                block=block_ms,
            )
        except Exception:
            logger.warning(
                "Failed to read relay events for hub %s from Redis stream",
                hub_id,
                exc_info=True,
            )
            return []
        if not rows:
            return []

        entries: list[tuple[str, dict]] = []
        for row in rows:
            entries.extend(self._parse_read_row(row))
        return entries

    def _parse_read_row(self, row: Any) -> list[tuple[str, dict]]:
        if isinstance(row, dict):
            entry = self._parse_row(row)
            return [] if entry is None else [entry]

        if not isinstance(row, tuple) or len(row) != 2:
            return []

        _stream_name, stream_entries = row
        if not isinstance(stream_entries, list | tuple):
            return []

        entries: list[tuple[str, dict]] = []
        for stream_entry in stream_entries:
            if not isinstance(stream_entry, tuple) or len(stream_entry) != 2:
                continue
            entry_id, fields = stream_entry
            entry = self._parse_entry(entry_id, fields or {})
            if entry is not None:
                entries.append(entry)
        return entries

    def _parse_row(self, row: dict[str, Any]) -> tuple[str, dict] | None:
        entry_id = row.get("id")
        fields = row.get("fields") or {}
        return self._parse_entry(entry_id, fields)

    def _parse_entry(self, entry_id: Any, fields: Any) -> tuple[str, dict] | None:
        if not isinstance(entry_id, str) or not isinstance(fields, dict):
            return None
        payload = fields.get("payload", "{}")
        try:
            event = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            logger.warning(
                "Skipping malformed relay stream payload for entry %s",
                entry_id,
                exc_info=True,
            )
            return None
        return entry_id, event

    async def record_heartbeat(self, hub_id: str) -> None:
        if self._kv is None:
            return None
        try:
            await self._kv.set(
                f"{self.HEARTBEAT_PREFIX}{hub_id}",
                "1",
                ttl=self._heartbeat_ttl,
            )
        except Exception:
            logger.warning(
                "Failed to record heartbeat for hub %s in Redis",
                hub_id,
                exc_info=True,
            )
        return None

    async def is_hub_alive(self, hub_id: str) -> bool:
        if self._kv is None:
            return False
        try:
            return await self._kv.exists(f"{self.HEARTBEAT_PREFIX}{hub_id}")
        except Exception:
            logger.warning(
                "Failed to read heartbeat for hub %s from Redis",
                hub_id,
                exc_info=True,
            )
            return False


__all__ = ["RelayStreamService"]
