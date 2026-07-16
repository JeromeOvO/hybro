from __future__ import annotations

from common.utils.time import utcnow
from models.orchestration import (
    TERMINAL_ORCHESTRATION_STATUSES,
    OrchestrationRunState,
    OrchestrationStatus,
)


class OrchestrationTransitionError(ValueError):
    pass


def mark_running(state: OrchestrationRunState) -> OrchestrationRunState:
    if state.status in TERMINAL_ORCHESTRATION_STATUSES:
        raise OrchestrationTransitionError("terminal runs cannot be resumed")
    updated = state.model_copy(deep=True)
    updated.status = OrchestrationStatus.RUNNING
    updated.state_version += 1
    updated.updated_at = utcnow()
    return updated


def mark_terminal(
    state: OrchestrationRunState,
    status: OrchestrationStatus | str,
    *,
    reason: str,
) -> OrchestrationRunState:
    if state.status in TERMINAL_ORCHESTRATION_STATUSES:
        raise OrchestrationTransitionError("terminal runs cannot be rewritten")
    terminal_status = OrchestrationStatus(status)
    if terminal_status not in TERMINAL_ORCHESTRATION_STATUSES:
        raise OrchestrationTransitionError(f"{terminal_status} is not terminal")
    updated = state.model_copy(deep=True)
    updated.status = terminal_status
    updated.terminal_reason = reason
    updated.state_version += 1
    updated.updated_at = utcnow()
    return updated
