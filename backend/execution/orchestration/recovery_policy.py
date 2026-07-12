from __future__ import annotations

from execution.orchestration.goal_fingerprinting import target_goal_fingerprints
from execution.orchestration.outcome_policy import OutcomeHistoryView
from models.orchestration import (
    BlockerRecord,
    OrchestrationRunState,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
    PlannerQuestion,
)


def recovery_directives(state: OrchestrationRunState) -> list[dict[str, object]]:
    validated_blockers = [
        blocker
        for blocker in state.blockers
        if blocker.status == "open"
        and blocker.validation_status == "validated"
        and blocker.validated_user_only
    ]
    if validated_blockers:
        return [
            {
                "code": "ask_user_for_validated_blocker",
                "blocker_keys": [
                    blocker.key
                    for blocker in sorted(validated_blockers, key=lambda item: item.key)
                ],
                "reason": "Validated user-only blocker is open.",
            }
        ]

    latest_no_progress = [
        outcome
        for outcome in state.delegation_outcomes
        if outcome.status == "no_progress"
    ]
    if latest_no_progress:
        latest = latest_no_progress[-1]
        return [
            {
                "code": "avoid_same_agent_same_goal_revision",
                "agent_id": latest.agent_id,
                "goal_revision_fingerprint": latest.goal_revision_fingerprint,
                "reason": "Latest Agent invocation made no required progress.",
            }
        ]
    return []


def action_for_rejected_delegate(
    state: OrchestrationRunState,
    *,
    error_code: str,
) -> PlannerAction | None:
    if error_code not in {
        "delegate_blocked_pending_user",
        "delegate_no_progress_repeat",
        "delegate_repair_lineage_required",
    }:
        return None
    validated_blockers = [
        blocker
        for blocker in state.blockers
        if blocker.status == "open"
        and blocker.validation_status == "validated"
        and blocker.validated_user_only
    ]
    if not validated_blockers:
        return None
    sorted_blockers = sorted(validated_blockers, key=lambda item: item.key)
    blocker_obligations = {
        blocker.key: _required_obligations_for_blocker(state, blocker)
        for blocker in sorted_blockers
    }
    blocker_keys = [blocker.key for blocker in sorted_blockers]
    required_obligation_keys = sorted(
        {
            obligation
            for obligations in blocker_obligations.values()
            for obligation in obligations
        }
    )
    return PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="Backend recovery selected HITL because validated user-only blockers are open.",
        questions=[
            PlannerQuestion(
                prompt="\n".join(blocker.description for blocker in sorted_blockers),
                reason="blocker",
                blocker_keys=blocker_keys,
                required_obligation_keys=required_obligation_keys,
                blocker_obligations=blocker_obligations,
            )
        ],
    )


def _required_obligations_for_blocker(
    state: OrchestrationRunState,
    blocker: BlockerRecord,
) -> list[str]:
    blocked_outputs = set(blocker.blocked_output_keys)
    obligations: list[str] = []
    for outcome in reversed(state.delegation_outcomes):
        for obligation in outcome.remaining_required_obligations:
            output_key = obligation.split(":", 1)[0]
            if not blocked_outputs or output_key in blocked_outputs:
                obligations.append(obligation)
        if obligations:
            break
    return sorted(dict.fromkeys(obligations))


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
