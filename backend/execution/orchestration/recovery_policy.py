from __future__ import annotations

from execution.orchestration.goal_fingerprinting import target_goal_fingerprints
from execution.orchestration.outcome_policy import OutcomeHistoryView
from models.orchestration import (
    OrchestrationRunState,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
)


def normalize_delegate_repair_lineage(
    action: PlannerAction,
    state: OrchestrationRunState,
    resource_fingerprints: dict[str, str],
) -> PlannerAction:
    if action.action != PlannerActionType.DELEGATE:
        return action
    targets = [
        _normalize_target_repair_lineage(target, state, resource_fingerprints)
        for target in action.targets
    ]
    return action.model_copy(update={"targets": targets}, deep=True)


def _normalize_target_repair_lineage(
    target: PlannedDelegateTarget,
    state: OrchestrationRunState,
    resource_fingerprints: dict[str, str],
) -> PlannedDelegateTarget:
    if target.repair_of_intent_id:
        return target
    fingerprints = target_goal_fingerprints(target, resource_fingerprints)
    chain = OutcomeHistoryView.from_state(state).chain(
        target.agent_id,
        fingerprints.goal_revision_fingerprint,
    )
    latest = chain.latest_outcome
    if latest is None:
        return target
    if latest.status == "failed":
        return target
    if latest.status == "blocked":
        return target
    if latest.status not in {"partial", "no_progress"}:
        return target
    return target.model_copy(
        update={"repair_of_intent_id": latest.dispatch_intent_id},
        deep=True,
    )
