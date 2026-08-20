"""Pure AgentCall lifecycle transition rules."""

from __future__ import annotations

from .models import AgentCallState

TERMINAL_AGENT_CALL_STATES = frozenset(
    {"completed", "failed", "canceled", "rejected", "expired"}
)
ACTIVE_AGENT_CALL_STATES = frozenset(
    {
        "accepted",
        "dispatching",
        "working",
        "continuation_pending",
        "input_required",
        "auth_required",
        "resuming",
    }
)

AGENT_CALL_TRANSITIONS: dict[AgentCallState, frozenset[AgentCallState]] = {
    "accepted": frozenset({"dispatching", "canceled", "rejected", "expired"}),
    "dispatching": frozenset(
        {
            "working",
            "completed",
            "continuation_pending",
            "failed",
            "canceled",
            "rejected",
            "expired",
        }
    ),
    "working": frozenset(
        {"completed", "continuation_pending", "failed", "canceled", "expired"}
    ),
    "continuation_pending": frozenset(
        {
            "resuming",
            "input_required",
            "auth_required",
            "failed",
            "canceled",
            "expired",
        }
    ),
    "input_required": frozenset({"resuming", "canceled", "expired"}),
    "auth_required": frozenset({"resuming", "canceled", "expired"}),
    "resuming": frozenset(
        {
            "working",
            "completed",
            "continuation_pending",
            "failed",
            "canceled",
            "expired",
        }
    ),
    "completed": frozenset(),
    "failed": frozenset(),
    "canceled": frozenset(),
    "rejected": frozenset(),
    "expired": frozenset(),
}


class IllegalAgentCallTransition(ValueError):
    """Raised when a state change is outside the frozen transition table."""


def is_legal_agent_call_transition(
    from_state: AgentCallState, to_state: AgentCallState
) -> bool:
    """Return whether one AgentCall state may transition to another."""

    return to_state in AGENT_CALL_TRANSITIONS[from_state]


def validate_agent_call_transition(
    from_state: AgentCallState, to_state: AgentCallState
) -> None:
    """Raise for an illegal transition without mutating state."""

    if not is_legal_agent_call_transition(from_state, to_state):
        raise IllegalAgentCallTransition(
            f"illegal AgentCall transition: {from_state} -> {to_state}"
        )
