from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from models.orchestration import (
    BlockerRecord,
    DelegationOutcomeRecord,
    OrchestrationRunState,
    PlannedDelegateTarget,
)


@dataclass(frozen=True)
class AttemptChainView:
    agent_id: str
    goal_revision_fingerprint: str
    same_agent_attempt_number: int
    required_progress_epoch: int
    no_progress_repair_used_in_epoch: bool
    latest_outcome: DelegationOutcomeRecord | None


@dataclass(frozen=True)
class RetryDecision:
    allowed: bool
    kind: Literal["initial", "semantic_repair", "operational_retry", "alternate_agent"]
    code: str | None = None


@dataclass(frozen=True)
class BlockerValidationDecision:
    valid: bool
    code: str


@dataclass(frozen=True)
class OutcomeHistoryView:
    outcomes: tuple[DelegationOutcomeRecord, ...]
    intents_by_id: MappingProxyType

    @classmethod
    def from_state(cls, state: OrchestrationRunState) -> OutcomeHistoryView:
        return cls(
            outcomes=tuple(
                outcome.model_copy(deep=True) for outcome in state.delegation_outcomes
            ),
            intents_by_id=MappingProxyType(
                {
                    intent.dispatch_intent_id: intent.model_copy(deep=True)
                    for intent in state.dispatch_intents
                }
            ),
        )

    def chain(self, agent_id: str, goal_revision_fingerprint: str) -> AttemptChainView:
        chain_outcomes = [
            outcome
            for outcome in self.outcomes
            if outcome.agent_id == agent_id
            and outcome.goal_revision_fingerprint == goal_revision_fingerprint
        ]
        epoch = 0
        repair_used = False
        previous_remaining: set[str] | None = None
        for outcome in chain_outcomes:
            remaining = set(outcome.remaining_required_obligations)
            reduced = (
                bool(outcome.newly_satisfied_required_obligations)
                if previous_remaining is None
                else remaining < previous_remaining
            )
            if reduced:
                epoch += 1
                repair_used = False
            elif self._is_repair(outcome.dispatch_intent_id):
                repair_used = True
            previous_remaining = remaining
        return AttemptChainView(
            agent_id=agent_id,
            goal_revision_fingerprint=goal_revision_fingerprint,
            same_agent_attempt_number=len(chain_outcomes),
            required_progress_epoch=epoch,
            no_progress_repair_used_in_epoch=repair_used,
            latest_outcome=chain_outcomes[-1] if chain_outcomes else None,
        )

    def _is_repair(self, dispatch_intent_id: str) -> bool:
        intent = self.intents_by_id.get(dispatch_intent_id)
        return bool(intent and intent.repair_of_intent_id)


class BlockerPolicyValidator:
    def validate(
        self,
        blocker: BlockerRecord,
        *,
        required_output_keys: set[str],
        available_resource_refs: set[str] | None = None,
        eligible_alternate_agent_ids: set[str] | None = None,
        conditional_result_viable: bool = False,
    ) -> BlockerValidationDecision:
        if not set(blocker.blocked_output_keys) & required_output_keys:
            return BlockerValidationDecision(False, "blocker_not_required_output")
        if blocker.status != "open":
            return BlockerValidationDecision(False, "blocker_not_open")
        if not blocker.claimed_user_only:
            return BlockerValidationDecision(False, "blocker_candidate_unvalidated")
        blocked_required_output_keys = (
            set(blocker.blocked_output_keys) & required_output_keys
        )
        attempts_by_kind = _terminal_attempt_coverage(
            blocker, blocked_required_output_keys
        )
        if available_resource_refs is None:
            return BlockerValidationDecision(
                False, "blocker_resource_resolution_context_required"
            )
        resources = available_resource_refs
        if _resolution_coverage_incomplete(
            resources, attempts_by_kind["resource"], blocked_required_output_keys
        ):
            return BlockerValidationDecision(
                False, "blocker_resource_resolution_required"
            )
        if eligible_alternate_agent_ids is None:
            return BlockerValidationDecision(
                False, "blocker_alternate_agent_context_required"
            )
        agents = eligible_alternate_agent_ids
        if _resolution_coverage_incomplete(
            agents, attempts_by_kind["agent"], blocked_required_output_keys
        ):
            return BlockerValidationDecision(False, "blocker_alternate_agent_available")
        if conditional_result_viable:
            return BlockerValidationDecision(False, "blocker_conditional_result_viable")
        conditional_result_code = _conditional_result_validation_code(
            blocked_required_output_keys, attempts_by_kind["conditional_result"]
        )
        if conditional_result_code:
            return BlockerValidationDecision(False, conditional_result_code)
        return BlockerValidationDecision(True, "blocker_user_only_validated")


def duplicate_delegate_target_code(
    targets: list[PlannedDelegateTarget], goal_family_fingerprints: list[str]
) -> str | None:
    pairs = zip(targets, goal_family_fingerprints, strict=True)
    seen: set[tuple[str, str]] = set()
    for target, goal_family_fingerprint in pairs:
        pair = (target.agent_id, goal_family_fingerprint)
        if pair in seen:
            return "duplicate_delegate_goal_target"
        seen.add(pair)
    return None


def evaluate_retry(
    run_state: OrchestrationRunState,
    target: PlannedDelegateTarget,
    goal_family_fingerprint: str,
    goal_revision_fingerprint: str,
) -> RetryDecision:
    history = OutcomeHistoryView.from_state(run_state)
    revision_outcomes = [
        outcome
        for outcome in history.outcomes
        if outcome.goal_family_fingerprint == goal_family_fingerprint
        and outcome.goal_revision_fingerprint == goal_revision_fingerprint
    ]
    if any(outcome.status == "fulfilled" for outcome in revision_outcomes):
        return _rejected("delegate_goal_already_fulfilled")
    chain = history.chain(target.agent_id, goal_revision_fingerprint)
    latest = chain.latest_outcome
    if latest is None:
        kind: Literal["initial", "alternate_agent"] = (
            "alternate_agent" if revision_outcomes else "initial"
        )
        return RetryDecision(True, kind)
    if latest.status == "failed":
        return _evaluate_operational_retry(run_state, latest, target)
    if target.repair_of_intent_id != latest.dispatch_intent_id:
        return _rejected("delegate_repair_lineage_required")
    if chain.no_progress_repair_used_in_epoch:
        return _rejected("delegate_no_progress_repeat")
    return RetryDecision(True, "semantic_repair")


def active_completion_scope(
    run_state: OrchestrationRunState,
    referenced_disposition_event_ids: set[str],
) -> set[tuple[str, str]]:
    dispositions_by_id = {
        disposition.event_id: disposition
        for disposition in run_state.goal_family_dispositions
    }
    unknown_event_ids = referenced_disposition_event_ids - dispositions_by_id.keys()
    if unknown_event_ids:
        raise ValueError(
            "Unknown goal family disposition event IDs: "
            + ", ".join(sorted(unknown_event_ids))
        )
    family_outcomes: dict[str, list[DelegationOutcomeRecord]] = {}
    for outcome in run_state.delegation_outcomes:
        family_outcomes.setdefault(outcome.goal_family_fingerprint, []).append(outcome)
    active: set[tuple[str, str]] = set()
    for family, outcomes in family_outcomes.items():
        latest = outcomes[-1]
        latest_index = len(outcomes) - 1
        disposed_through_latest = any(
            disposition.goal_family_fingerprint == family
            and _revision_index(outcomes, disposition.through_goal_revision_fingerprint)
            >= latest_index
            for event_id, disposition in dispositions_by_id.items()
            if event_id in referenced_disposition_event_ids
        )
        if not disposed_through_latest:
            active.add((family, latest.goal_revision_fingerprint))
    return active


def _evaluate_operational_retry(
    run_state: OrchestrationRunState,
    latest: DelegationOutcomeRecord,
    target: PlannedDelegateTarget,
) -> RetryDecision:
    open_failures_by_id = {
        failure.failure_id: failure
        for failure in run_state.open_failures
        if failure.status == "open" and failure.recoverable
    }
    failures = [
        open_failures_by_id[failure_id]
        for failure_id in latest.open_failure_ids
        if failure_id in open_failures_by_id
    ]
    if not failures:
        prior_intent = next(
            (
                intent
                for intent in run_state.dispatch_intents
                if intent.dispatch_intent_id == latest.dispatch_intent_id
            ),
            None,
        )
        if prior_intent is not None and prior_intent.task == target.task:
            failures = [
                failure
                for failure in open_failures_by_id.values()
                if failure.dispatch_intent_id == latest.dispatch_intent_id
            ]
    if not failures:
        return _rejected_operational("recovery_retry_unavailable")
    if all(failure.retry_count >= failure.max_retries for failure in failures):
        return _rejected_operational("recovery_retry_exhausted")
    return RetryDecision(True, "operational_retry")


def _revision_index(outcomes: list[DelegationOutcomeRecord], revision: str) -> int:
    for index in range(len(outcomes) - 1, -1, -1):
        if outcomes[index].goal_revision_fingerprint == revision:
            return index
    return -1


def _terminal_attempt_coverage(
    blocker: BlockerRecord,
    blocked_required_output_keys: set[str],
) -> dict[str, dict[str, set[str]]]:
    coverage: dict[str, dict[str, set[str]]] = {
        kind: {} for kind in ("resource", "agent", "conditional_result")
    }
    for attempt in blocker.resolution_attempts:
        if attempt.outcome not in {"unavailable", "insufficient", "failed"}:
            continue
        keys = set(attempt.applies_to_output_keys) & blocked_required_output_keys
        coverage[attempt.kind].setdefault(attempt.reference_id, set()).update(keys)
    return coverage


def _resolution_coverage_incomplete(
    references: set[str],
    coverage_by_reference: dict[str, set[str]],
    required_output_keys: set[str],
) -> bool:
    if not references:
        return False
    if references - coverage_by_reference.keys():
        return True
    covered_output_keys = set().union(
        *(coverage_by_reference[reference_id] for reference_id in references)
    )
    return not required_output_keys <= covered_output_keys


def _conditional_result_validation_code(
    blocked_required_output_keys: set[str],
    coverage_by_reference: dict[str, set[str]],
) -> str | None:
    attempted_output_keys = (
        set().union(*coverage_by_reference.values()) if coverage_by_reference else set()
    )
    if not coverage_by_reference:
        return "blocker_conditional_result_resolution_required"
    if blocked_required_output_keys - attempted_output_keys:
        return "blocker_conditional_result_output_required"
    return None


def _rejected(code: str) -> RetryDecision:
    return RetryDecision(False, "semantic_repair", code)


def _rejected_operational(code: str) -> RetryDecision:
    return RetryDecision(False, "operational_retry", code)
