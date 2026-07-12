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
    latest = chain.latest_outcome or _latest_equivalent_target_outcome(
        target,
        state,
        resource_fingerprints,
        fingerprints.goal_revision_fingerprint,
    )
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


def _latest_equivalent_target_outcome(
    target: PlannedDelegateTarget,
    state: OrchestrationRunState,
    resource_fingerprints: dict[str, str],
    goal_revision_fingerprint: str,
):
    intents_by_id = {
        intent.dispatch_intent_id: intent for intent in state.dispatch_intents
    }
    for outcome in reversed(state.delegation_outcomes):
        if outcome.agent_id != target.agent_id:
            continue
        intent = intents_by_id.get(outcome.dispatch_intent_id)
        if intent is None:
            continue
        prior_target = PlannedDelegateTarget(
            agent_id=intent.agent_id,
            task=intent.task,
            context_refs=intent.context_refs,
            artifact_refs=intent.artifact_refs,
            attachment_refs=intent.attachment_refs,
            expected_outputs=intent.expected_outputs,
        )
        if (
            target_goal_fingerprints(
                prior_target,
                resource_fingerprints,
            ).goal_revision_fingerprint
            == goal_revision_fingerprint
        ):
            return outcome
    return None
