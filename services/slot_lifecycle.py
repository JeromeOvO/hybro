"""SlotLifecycleManager — single termination point for content slots.

Ensures each slot is terminated at most once via Redis-backed idempotency.
See spec: docs/superpowers/specs/2026-04-11-room-message-area-redesign.md Section 7.2
"""

from __future__ import annotations

from typing import Any

from infrastructure.redis_service import RedisService
from services.turn_event_service import TurnEventAppender

SLOT_TERMINATED_TTL = 3600  # 1 hour


class SlotLifecycleManager:
    def __init__(self, appender: TurnEventAppender, redis: RedisService):
        self._appender = appender
        self._redis = redis

    async def open_slot(
        self,
        room_id: str,
        turn_id: str,
        slot_id: str,
        slot_type: str,
        **kwargs: Any,
    ) -> None:
        await self._appender.append(
            room_id, turn_id, "slot_opened",
            {"slot_id": slot_id, "slot_type": slot_type, **kwargs},
        )

    async def terminate_slot(
        self,
        room_id: str,
        turn_id: str,
        slot_id: str,
        status: str,
        content: str | None = None,
        artifacts: list[dict] | None = None,
        error: str | None = None,
        has_partial_content: bool | None = None,
    ) -> None:
        key = f"slot_terminated:{turn_id}:{slot_id}"
        acquired = await self._redis.set_nx(key, status, ex=SLOT_TERMINATED_TTL)
        if not acquired:
            return

        if content or artifacts:
            await self._appender.append(
                room_id, turn_id, "slot_snapshot",
                {
                    "slot_id": slot_id,
                    "content": content or "",
                    "artifacts": artifacts or [],
                },
            )

        await self._appender.append(
            room_id, turn_id, "slot_terminated",
            {
                "slot_id": slot_id,
                "status": status,
                "error": error,
                "has_partial_content": has_partial_content,
            },
        )
