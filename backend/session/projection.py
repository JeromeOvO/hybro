"""Client-facing session state.

The projection never reads the approval question: a paused run is reported
as ``waiting_on_owner`` with the paused agent's id only.
"""

from __future__ import annotations

from models.session import SessionState
from session.waiter import CorrelatedOutcome

RUN_NOT_STARTED = "run_not_started"
BUSY = "busy"


def resolve_state(
    outcome: CorrelatedOutcome | None, *, closed: bool
) -> tuple[SessionState, str | None]:
    """Map a request's correlated events to the client state and reason."""
    if closed:
        return "closed", None
    if outcome is None or outcome.not_started or not outcome.seen:
        return "failed", RUN_NOT_STARTED
    settlement = outcome.settlement
    if settlement is not None:
        status = settlement.get("status")
        if status == "completed":
            return "completed", None
        if status == "canceled":
            if outcome.rejected:
                return "rejected", None
            code = settlement.get("cancellation_code")
            return "failed", f"canceled:{code}" if code else "canceled"
        summary = settlement.get("error_summary")
        code = settlement.get("failure_code")
        return "failed", summary if isinstance(summary, str) else code
    if outcome.waiting_input:
        return "waiting_on_owner", None
    return "working", None


def final_message_id(outcome: CorrelatedOutcome | None) -> str | None:
    if outcome is None or outcome.settlement is None:
        return None
    if outcome.settlement.get("status") != "completed":
        return None
    value = outcome.settlement.get("final_message_id")
    return value if isinstance(value, str) and value else None


def busy_state(states: list[str]) -> SessionState:
    """The state of an earlier, still-active run that refused a send."""
    if any(state == "awaiting_input" for state in states):
        return "waiting_on_owner"
    return "working"


__all__ = ["BUSY", "RUN_NOT_STARTED", "busy_state", "final_message_id", "resolve_state"]
