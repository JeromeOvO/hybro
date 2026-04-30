"""Pure run state transition validation (normative FSM subset for v1 orchestration runs)."""

from __future__ import annotations

from models.run import RunEventType, RunState, TERMINAL_RUN_STATES


class RunTransitionError(ValueError):
    """Illegal transition for the persisted run FSM."""


def ensure_transition_allowed(before: RunState, after: RunState) -> None:
    """Raise RunTransitionError if moving from *before* to *after* is not allowed."""
    if before == after:
        return
    if before in TERMINAL_RUN_STATES:
        raise RunTransitionError(f"cannot leave terminal state {before!r} -> {after!r}")

    allowed: dict[RunState, set[RunState]] = {
        RunState.QUEUED: {
            RunState.PROCESSING,
            RunState.AWAITING_INPUT,
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELED,
        },
        RunState.PROCESSING: {
            RunState.AWAITING_INPUT,
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELED,
        },
        RunState.AWAITING_INPUT: {
            RunState.PROCESSING,
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELED,
        },
    }
    if after not in allowed.get(before, set()):
        raise RunTransitionError(f"illegal transition {before!r} -> {after!r}")


def next_state_for_terminal_event(event_type: RunEventType) -> RunState:
    if event_type == RunEventType.RUN_COMPLETED:
        return RunState.COMPLETED
    if event_type == RunEventType.RUN_FAILED:
        return RunState.FAILED
    if event_type == RunEventType.RUN_CANCELED:
        return RunState.CANCELED
    raise RunTransitionError(f"not a terminal event: {event_type!r}")
