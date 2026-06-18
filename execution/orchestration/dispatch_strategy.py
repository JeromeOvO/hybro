from __future__ import annotations

from enum import StrEnum


class DispatchStrategy(StrEnum):
    """Dispatch strategy resolved after agent selection."""

    SINGLE = "single"
    SEQUENTIAL = "sequential"
    SEQUENTIAL_DEBATE = "sequential_debate"
    SUPERVISOR = "supervisor"


def resolve_strategy(
    use_supervisor: bool,
    is_debate_mode: bool,
    agent_count: int,
) -> DispatchStrategy:
    """Resolve dispatch strategy from room flags and agent count."""
    if use_supervisor:
        return DispatchStrategy.SUPERVISOR
    if is_debate_mode:
        return DispatchStrategy.SEQUENTIAL_DEBATE
    if agent_count > 1:
        return DispatchStrategy.SEQUENTIAL
    return DispatchStrategy.SINGLE


__all__ = ["DispatchStrategy", "resolve_strategy"]
