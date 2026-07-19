from __future__ import annotations

from collections.abc import Sequence

from common.utils.time import utcnow
from models.orchestration import (
    TERMINAL_DISPATCH_STATUSES,
    TERMINAL_ORCHESTRATION_STATUSES,
    ActiveDispatchRef,
    DispatchIntent,
    OrchestrationRunState,
    OrchestrationStatus,
    PlannerAction,
    PlannerActionRecord,
)
from models.supervisor import StepResult


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


def _bump(updated: OrchestrationRunState) -> OrchestrationRunState:
    updated.state_version += 1
    updated.updated_at = utcnow()
    return updated


def record_planner_action(
    state: OrchestrationRunState,
    action: PlannerAction,
) -> OrchestrationRunState:
    updated = state.model_copy(deep=True)
    updated.steps_used += 1
    updated.last_planner_action = PlannerActionRecord(
        action=str(action.action),
        reasoning=action.reasoning,
    )
    updated.decision_log.append(
        {
            "action": str(action.action),
            "reasoning": action.reasoning,
            "targets": [
                target.model_dump(mode="json") for target in action.targets
            ],
            "planner_action": action.model_dump(mode="json"),
            "created_at": utcnow().isoformat(),
        }
    )
    return _bump(updated)


def record_dispatch_intents(
    state: OrchestrationRunState,
    intents: Sequence[DispatchIntent],
) -> OrchestrationRunState:
    updated = state.model_copy(deep=True)
    updated.status = OrchestrationStatus.DISPATCHING
    updated.dispatch_intents.extend(intents)
    active_by_message_id = {
        active.agent_message_id: active for active in updated.active_dispatches
    }
    for intent in intents:
        active_by_message_id[intent.planned_agent_message_id] = ActiveDispatchRef(
            agent_message_id=intent.planned_agent_message_id,
            agent_id=intent.agent_id,
            status=intent.status,
        )
    updated.active_dispatches = list(active_by_message_id.values())
    return _bump(updated)


def record_step_result_metadata(
    state: OrchestrationRunState,
    result: StepResult,
    *,
    status: OrchestrationStatus,
    matched_intent_id: str | None,
    advance_step: bool,
) -> OrchestrationRunState:
    """Record result metadata without double-counting a planner-owned step.

    Callers that already used ``record_planner_action`` for the logical step must
    pass ``advance_step=False``. The flag is reserved for compatibility paths
    that persist a standalone result without a corresponding planner transition.
    """
    updated = state.model_copy(deep=True)
    updated.status = status
    if advance_step:
        updated.steps_used += 1
    result_status = str(
        result.status.value if hasattr(result.status, "value") else result.status
    )
    matched_message_ids = (
        {result.agent_message_id} if result.agent_message_id else set()
    )
    for intent in updated.dispatch_intents:
        if matched_intent_id and intent.dispatch_intent_id == matched_intent_id:
            intent.status = result_status
            matched_message_ids.add(intent.planned_agent_message_id)
    for active in updated.active_dispatches:
        if active.agent_message_id in matched_message_ids:
            active.status = result_status
    updated.active_dispatches = [
        active
        for active in updated.active_dispatches
        if active.status not in TERMINAL_DISPATCH_STATUSES
    ]
    return _bump(updated)
