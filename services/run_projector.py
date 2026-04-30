"""Project room.processing_message_id from non-terminal runs (design §N.1)."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from common.utils.logger import get_logger
from services.database_service import db_service
from services.run_metrics import increment_counter

logger = get_logger(__name__)


def _feature_run_parity_log_enabled() -> bool:
    return (os.environ.get("FEATURE_RUN_PARITY_LOG") or "").strip() == "1"


def _parse_created_at(doc: dict[str, Any]) -> datetime:
    raw = doc.get("created_at")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    # Fallback: treat as "now" ordering last
    from common.utils.time import utcnow

    return utcnow()


def compute_processing_message_id_mirror(active_runs: list[dict[str, Any]]) -> str | None:
    """§N.1: mirror from non-terminal runs for the room (single-valued legacy field)."""
    if not active_runs:
        return None
    with_trigger = [r for r in active_runs if r.get("trigger_message_id")]
    if not with_trigger:
        return None
    with_trigger.sort(
        key=lambda r: (_parse_created_at(r), str(r.get("run_id") or "")),
    )
    first = with_trigger[0]
    tid = first.get("trigger_message_id")
    return str(tid) if tid else None


async def sync_room_processing_mirror(room_id: str) -> str | None:
    """Recompute and persist processing_message_id from runs; returns new mirror value."""
    if not room_id:
        return None
    from database.mongodb import mongodb

    active = await mongodb.get_active_runs_by_room_id(room_id)
    desired = compute_processing_message_id_mirror(active)
    room = await mongodb.get_room_by_room_id(room_id)
    current = room.processing_message_id if room else None

    if _feature_run_parity_log_enabled() and current != desired:
        increment_counter("parity_legacy_processing_vs_run_mismatch_total")
        logger.info(
            "run_projector parity: room=%s mirror current=%r desired=%r active_runs=%d",
            room_id,
            current,
            desired,
            len(active),
        )

    await db_service.update_room_processing_status(room_id, desired)
    return desired
