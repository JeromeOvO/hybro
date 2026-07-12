from __future__ import annotations

from typing import Any

from models.orchestration import OrchestrationRunState


def build_terminal_summary(
    state: OrchestrationRunState,
    *,
    reason: str,
) -> dict[str, Any]:
    latest_outcome = state.delegation_outcomes[-1] if state.delegation_outcomes else None
    validated_blockers = [
        blocker
        for blocker in state.blockers
        if blocker.status == "open"
        and blocker.validation_status == "validated"
        and blocker.validated_user_only
    ]
    open_failures = [
        failure
        for failure in state.open_failures
        if failure.status == "open"
    ]
    if validated_blockers:
        recommended_next_action = "ask_user"
    elif latest_outcome is not None and latest_outcome.status == "no_progress":
        recommended_next_action = "delegate_alternate_agent_or_fail"
    elif open_failures:
        recommended_next_action = "retry_or_fail"
    else:
        recommended_next_action = "inspect_orchestration_state"
    return {
        "code": "orchestration_failed",
        "reason": reason,
        "last_outcome_id": latest_outcome.outcome_id if latest_outcome else None,
        "last_outcome_status": latest_outcome.status if latest_outcome else None,
        "remaining_required_obligations": (
            latest_outcome.remaining_required_obligations if latest_outcome else []
        ),
        "validated_blocker_keys": [blocker.key for blocker in validated_blockers],
        "open_failure_codes": [failure.error_code for failure in open_failures],
        "recommended_next_action": recommended_next_action,
    }
