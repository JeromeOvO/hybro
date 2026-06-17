from __future__ import annotations

from typing import Any

from common.utils.logger import get_logger
from context_memory import assembly
from context_memory.translators import primitive, turn_from_dict

logger = get_logger(__name__)


def select_legacy_turns_within_budget(
    turns: list,
    budget_tokens: int,
    summary: str | None = None,
) -> tuple[list, int]:
    turn_data = [turn_from_dict(primitive(turn)) for turn in turns]
    _selected, turns_truncated = assembly.select_turns_within_budget(
        turn_data,
        budget_tokens,
        summary=summary,
    )
    return turns[turns_truncated:], turns_truncated


def log_context_metrics(
    *,
    room_id: str,
    total_tokens: int,
    occupancy_pct: float,
    was_truncated: bool,
    truncation_reason: Any,
    turns_included: int,
    turns_truncated: int,
    context_type: str,
    budget_summary: dict[str, Any],
    full_turns: int = 0,
    compact_turns: int = 0,
    stable_prefix_tokens: int = 0,
) -> None:
    budget = budget_summary.get("available_for_content", 0)
    if occupancy_pct > 90:
        logger.error(
            f"EMERGENCY context occupancy [{context_type}] room={room_id}: "
            f"occupancy={occupancy_pct:.1f}% EXCEEDS 90%! "
            f"tokens={total_tokens}/{budget}, "
            f"cache_prefix_tokens={stable_prefix_tokens}, "
            f"truncated={turns_truncated} turns. Investigate verbose agent."
        )
    elif was_truncated or occupancy_pct > 85:
        logger.warning(
            f"Context TRUNCATED [{context_type}] room={room_id}: "
            f"occupancy={occupancy_pct:.1f}% (hard cap zone), "
            f"truncated={turns_truncated} turns, "
            f"tokens={total_tokens}/{budget}, "
            f"cache_prefix_tokens={stable_prefix_tokens}"
        )
    elif occupancy_pct > 70:
        logger.info(
            f"Context approaching limit [{context_type}] room={room_id}: "
            f"occupancy={occupancy_pct:.1f}% (>70%), "
            f"tokens={total_tokens}, cache_prefix_tokens={stable_prefix_tokens}, "
            f"turns={turns_included} (full={full_turns}, compact={compact_turns})"
        )
    else:
        logger.debug(
            f"Context assembled [{context_type}] room={room_id}: "
            f"occupancy={occupancy_pct:.1f}%, "
            f"tokens={total_tokens}, cache_prefix_tokens={stable_prefix_tokens}, "
            f"turns={turns_included} (full={full_turns}, compact={compact_turns})"
        )


def record_context_metrics(
    *,
    room_id: str,
    result: Any,
    context_type: str,
    budget_summary: dict[str, Any],
    metadata: dict | None = None,
) -> bool:
    metadata = metadata or {}
    log_context_metrics(
        room_id=room_id,
        total_tokens=result.total_tokens,
        occupancy_pct=result.occupancy_pct,
        was_truncated=result.was_truncated,
        truncation_reason=result.truncation_reason,
        turns_included=result.turns_included,
        turns_truncated=result.turns_truncated,
        context_type=context_type,
        budget_summary=budget_summary,
        full_turns=metadata.get("full_turns", 0),
        compact_turns=metadata.get("compact_turns", 0),
        stable_prefix_tokens=result.stable_prefix_tokens,
    )
    return bool(result.was_truncated)


__all__ = [
    "log_context_metrics",
    "record_context_metrics",
    "select_legacy_turns_within_budget",
]
