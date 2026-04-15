"""TurnEventAppender and TurnSeqCounter for the event-sourced turn architecture.

See spec: docs/superpowers/specs/2026-04-11-room-message-area-redesign.md Section 7.1
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from infrastructure.redis_service import RedisService
from models.turn_event import TurnEvent, _PAYLOAD_MAP

logger = logging.getLogger(__name__)

SEQ_TTL = 7200  # 2 hours
JOURNAL_DISABLED_TTL = 7200  # 2 hours


def generate_event_id() -> str:
    return f"evt_{uuid4().hex[:16]}"


def utcnow_ms() -> int:
    """Current UTC time in milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class TurnSeqCounter:
    """Per-turn monotonically increasing sequence counter backed by Redis INCR."""

    def __init__(self, redis: RedisService):
        self._redis = redis

    async def next(self, turn_id: str) -> int:
        return await self._redis.incr(f"turn_seq:{turn_id}")

    async def reset(self, turn_id: str) -> None:
        await self._redis.set_with_ttl(f"turn_seq:{turn_id}", "0", ex=SEQ_TTL)


class TurnNotStartedError(Exception):
    pass


class TurnEventAppender:
    """Centralized event service for the turn-based architecture.

    Phase 1 (dual-write): best-effort additive. Failures mark the turn as
    journal-disabled in Redis (cross-instance visible). Legacy SSE continues.

    Phase 3+ (legacy removed): all failures propagate. No silent degradation.
    """

    def __init__(
        self,
        sse_manager,
        db_service,
        seq_counter: TurnSeqCounter,
        redis: RedisService,
        *,
        dual_write_mode: bool = True,
    ):
        self._sse = sse_manager
        self._db = db_service
        self._seq = seq_counter
        self._redis = redis
        self._dual_write_mode = dual_write_mode

    async def _is_journal_disabled(self, turn_id: str) -> bool:
        return await self._redis.exists(f"turn_journal_disabled:{turn_id}")

    async def _disable_journal(self, turn_id: str) -> None:
        await self._redis.set_with_ttl(
            f"turn_journal_disabled:{turn_id}", "1", ex=JOURNAL_DISABLED_TTL
        )
        logger.error("Turn journal disabled for %s (partial or missing)", turn_id)

    async def start_turn(
        self,
        room_id: str,
        turn_id: str,
        user_input: dict,
        client_request_id: str,
    ) -> TurnEvent | None:
        """Create the first event of a turn. Forces seq = 1."""
        try:
            await self._seq.reset(turn_id)
            return await self._append_internal(
                room_id, turn_id, "turn_started",
                {"user_input": user_input},
                client_request_id=client_request_id,
            )
        except Exception:
            if self._dual_write_mode:
                await self._disable_journal(turn_id)
                return None
            raise

    async def append(
        self,
        room_id: str,
        turn_id: str,
        event_type: str,
        payload: dict,
        *,
        persist: bool = True,
        client_request_id: str | None = None,
    ) -> TurnEvent | None:
        if self._dual_write_mode:
            try:
                if await self._is_journal_disabled(turn_id):
                    return None
                return await self._append_internal(
                    room_id, turn_id, event_type, payload,
                    persist=persist, client_request_id=client_request_id,
                )
            except Exception:
                await self._disable_journal(turn_id)
                return None
        else:
            return await self._append_internal(
                room_id, turn_id, event_type, payload,
                persist=persist, client_request_id=client_request_id,
            )

    async def _append_internal(
        self,
        room_id: str,
        turn_id: str,
        event_type: str,
        payload: dict,
        *,
        persist: bool = True,
        client_request_id: str | None = None,
    ) -> TurnEvent:
        if event_type != "turn_started":
            if not await self._db.turn_exists(room_id, turn_id):
                raise TurnNotStartedError(
                    f"Turn {turn_id} not started in room {room_id}"
                )

        payload_cls = _PAYLOAD_MAP.get(event_type)
        if payload_cls is None:
            raise ValueError(f"Unknown event type: {event_type}")

        event = TurnEvent(
            event_id=generate_event_id(),
            turn_id=turn_id,
            seq=await self._seq.next(turn_id),
            ts=utcnow_ms(),
            type=event_type,
            payload=payload_cls.model_validate(payload),
            client_request_id=client_request_id,
        )

        if persist:
            await self._db.append_turn_event(room_id, turn_id, event)

        await self._sse.broadcast_turn_event(room_id, event)
        return event
