"""§N.1: derive legacy processing_message_id display value from non-terminal runs (pure)."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _parse_created_at(doc: dict[str, Any]) -> datetime:
    raw = doc.get("created_at")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    from common.utils.time import utcnow

    return utcnow()


def compute_processing_message_id_mirror(active_runs: list[dict[str, Any]]) -> str | None:
    """§N.1: derive single display message_id from non-terminal runs (pure; not persisted)."""
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
