from __future__ import annotations

from collections import Counter

from execution.orchestration.outcome_evaluator import canonical_content_fingerprint
from execution.orchestration.outcome_policy import active_completion_scope
from models.orchestration import (
    DelegationOutcomeRecord,
    GoalProgressRecord,
    OrchestrationRunState,
)


def rebuild_goal_progress(state: OrchestrationRunState) -> OrchestrationRunState:
    updated = state.model_copy(deep=True)
    invalidated = _invalidated_obligations(updated)
    active_scope = active_completion_scope(
        updated,
        {disposition.event_id for disposition in updated.goal_family_dispositions},
    )
    records: list[GoalProgressRecord] = []
    outcomes_by_scope: dict[tuple[str, str], list[DelegationOutcomeRecord]] = {}
    for outcome in updated.delegation_outcomes:
        scope = (outcome.goal_family_fingerprint, outcome.goal_revision_fingerprint)
        if scope not in active_scope:
            continue
        outcomes_by_scope.setdefault(scope, []).append(outcome)
    for (family, revision), outcomes in sorted(outcomes_by_scope.items()):
        latest = outcomes[-1]
        newly_satisfied = [
            obligation
            for outcome in outcomes
            for obligation in outcome.newly_satisfied_required_obligations
        ]
        resatisfied = {
            obligation
            for obligation, count in Counter(newly_satisfied).items()
            if count > 1
        }
        satisfied = {
            obligation
            for obligation in newly_satisfied
            if obligation not in invalidated
        } | resatisfied
        remaining = set(latest.remaining_required_obligations) | (
            invalidated
            & {
                obligation
                for outcome in outcomes
                for obligation in outcome.newly_satisfied_required_obligations
            }
        )
        records.append(
            GoalProgressRecord(
                progress_id="goal-progress:"
                + canonical_content_fingerprint(
                    {
                        "run_id": updated.run_id,
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
    updated.goal_progress = records
    return updated


def _invalidated_obligations(state: OrchestrationRunState) -> set[str]:
    return {
        obligation
        for entry in state.decision_log
        if entry.get("code") == "required_evidence_invalidated"
        for obligation in entry.get("obligation_keys", [])
        if isinstance(obligation, str)
    }
