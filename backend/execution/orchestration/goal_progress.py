from __future__ import annotations

from execution.orchestration.outcome_evaluator import canonical_content_fingerprint
from execution.orchestration.outcome_policy import active_completion_scope
from models.orchestration import (
    DelegationOutcomeRecord,
    GoalProgressRecord,
    OrchestrationRunState,
)


def rebuild_goal_progress(state: OrchestrationRunState) -> OrchestrationRunState:
    active_scope = active_completion_scope(
        state,
        {disposition.event_id for disposition in state.goal_family_dispositions},
    )
    records: list[GoalProgressRecord] = []
    outcomes_by_scope: dict[tuple[str, str], list[DelegationOutcomeRecord]] = {}
    for outcome in state.delegation_outcomes:
        scope = (outcome.goal_family_fingerprint, outcome.goal_revision_fingerprint)
        if scope not in active_scope:
            continue
        outcomes_by_scope.setdefault(scope, []).append(outcome)
    for (family, revision), outcomes in sorted(outcomes_by_scope.items()):
        latest = outcomes[-1]
        invalidated = _invalidated_obligations(state, family)
        satisfied = {
            obligation
            for outcome in state.delegation_outcomes
            if outcome.goal_family_fingerprint == family
            for obligation in outcome.newly_satisfied_required_obligations
        }
        remaining = set(latest.remaining_required_obligations) | invalidated
        records.append(
            GoalProgressRecord(
                progress_id="goal-progress:"
                + canonical_content_fingerprint(
                    {
                        "run_id": state.run_id,
                        "goal_family_fingerprint": family,
                        "goal_revision_fingerprint": revision,
                    }
                )[:20],
                goal_family_fingerprint=family,
                through_goal_revision_fingerprint=revision,
                latest_outcome_id=latest.outcome_id,
                source_outcome_ids=[outcome.outcome_id for outcome in outcomes],
                agent_ids=list(dict.fromkeys(outcome.agent_id for outcome in outcomes)),
                status=latest.status,
                satisfied_required_obligations=sorted(satisfied),
                remaining_required_obligations=sorted(remaining - satisfied),
                blocker_keys=[blocker.key for blocker in latest.blockers],
                unknown_keys=[unknown.key for unknown in latest.unknowns],
            )
        )
    return state.model_copy(update={"goal_progress": records})


def _invalidated_obligations(
    state: OrchestrationRunState,
    goal_family_fingerprint: str,
) -> set[str]:
    return {
        obligation
        for entry in state.decision_log
        if entry.get("code") == "required_evidence_invalidated"
        and entry.get("goal_family_fingerprint") == goal_family_fingerprint
        for obligation in entry.get("obligation_keys", [])
        if isinstance(obligation, str)
    }
