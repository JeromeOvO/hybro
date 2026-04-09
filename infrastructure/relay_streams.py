"""Redis Streams adapter for hub relay event delivery.

Each hub gets its own stream (hub:relay:{hub_id}). Events are pushed
by push_event() and consumed by read_events(). Heartbeat liveness
is tracked via a simple TTL key (hub:heartbeat:{hub_id}).
"""

from __future__ import annotations

import json
from infrastructure.redis_service import RedisService
from common.utils.logger import get_logger

logger = get_logger(__name__)


class RelayStreamService:
    """Redis Streams adapter for hub relay event delivery.

    Each hub gets its own stream (hub:relay:{hub_id}). Events are pushed
    by push_event() and consumed by read_events(). Heartbeat liveness
    is tracked via a simple TTL key (hub:heartbeat:{hub_id}).

    Hub Liveness State Machine (Redis Streams path)
    ------------------------------------------------
    AUTHORITATIVE SIGNAL: Redis TTL key (hub:heartbeat:{hub_id}).
    PERSISTED PROJECTION: MongoDB ``is_online`` flag (eventually consistent,
        may lag by up to one heartbeat interval).
    QUERY SURFACE: all real-time liveness queries MUST use
        ``_resolve_hub_liveness()`` (in RelayService), which always consults
        the authoritative Redis signal. MongoDB ``is_online`` is never
        consulted for liveness decisions; it is a projection maintained by
        ``_do_heartbeat_check`` for offline consumers (dashboards, batch
        exports).

    Signals that refresh the Redis TTL:
        1. Client ``POST /heartbeat`` (primary, proves hub application is
           healthy).
        2. Server-side SSE loop iteration (secondary, proves TCP connection
           is alive).
        3. ``connect_hub()`` initial connection setup.

    Transitions:
        Online -> Offline: Redis TTL expires, ``_do_heartbeat_check``
            corrects MongoDB and signals SSE disconnect (best-effort).
        Offline -> Online: ``connect_hub()`` (new SSE) OR
            ``_do_heartbeat_check`` self-heal (Redis alive, MongoDB stale).

    Invariant: if the Redis key exists the hub SHOULD be online. If the
    Redis key is absent but MongoDB says online, the hub is offline (stale
    flag that will be corrected on the next heartbeat check).
    """
    STREAM_PREFIX = "hub:relay:"
    HEARTBEAT_PREFIX = "hub:heartbeat:"

    def __init__(
        self, redis_service: RedisService, *, maxlen: int = 10_000, heartbeat_ttl: int = 90,
    ) -> None:
        self._redis = redis_service
        self._maxlen = maxlen
        self._heartbeat_ttl = heartbeat_ttl

    async def push_event(self, hub_id: str, event: dict) -> str | None:
        """Push an event to a hub's relay stream.

        Args:
            hub_id: Hub identifier
            event: Event dict to serialize and push

        Returns:
            Stream entry ID or None on error
        """
        stream = f"{self.STREAM_PREFIX}{hub_id}"
        entry_id = await self._redis.xadd(
            stream, {"payload": json.dumps(event)}, maxlen=self._maxlen,
        )
        return entry_id

    async def read_events(
        self, hub_id: str, last_id: str = "0-0", count: int = 10, block_ms: int = 5000,
    ) -> list[tuple[str, dict]]:
        """Read events from a hub's relay stream.

        Args:
            hub_id: Hub identifier
            last_id: Read events after this ID ("0-0" for all)
            count: Max events to read
            block_ms: Block timeout in milliseconds

        Returns:
            List of (entry_id, parsed_payload) tuples. Empty on timeout or error.
        """
        stream = f"{self.STREAM_PREFIX}{hub_id}"
        result = await self._redis.xread({stream: last_id}, count=count, block=block_ms)
        if not result:
            return []
        entries = []
        # result format: [(stream_name, [(entry_id, {field: value}), ...])]
        # Safe to index [0] — we always pass a single stream to xread.
        for entry_id, data in result[0][1]:
            payload = json.loads(data.get("payload", "{}"))
            entries.append((entry_id, payload))
        return entries

    async def record_heartbeat(self, hub_id: str) -> None:
        """Record hub heartbeat (sets TTL key)."""
        key = f"{self.HEARTBEAT_PREFIX}{hub_id}"
        ok = await self._redis.set_with_ttl(key, "1", ex=self._heartbeat_ttl)
        if not ok:
            logger.warning(
                "Failed to record heartbeat for hub %s in Redis (set_with_ttl returned falsy)",
                hub_id,
            )

    async def is_hub_alive(self, hub_id: str) -> bool:
        """Check if hub has a valid heartbeat."""
        key = f"{self.HEARTBEAT_PREFIX}{hub_id}"
        return await self._redis.exists(key)
