"""Deterministic orchestration completion and finalization policy."""

from __future__ import annotations

from enum import StrEnum

from models.orchestration import (
    TERMINAL_DISPATCH_STATUSES,
    CompletionEvidence,
    OrchestrationRunState,
    PlannerActionType,
)


class FinalizationMode(StrEnum):
    DIRECT_AGENT = "direct_agent"
    SYNTHESIS = "synthesis"
    PLATFORM = "platform"


_NON_BLOCKING_REFERENCE_FAILURE_CODES = {
    "attachment_ref_not_found",
    "context_ref_not_found",
    "artifact_ref_not_found",
    "dispatch_payload_ref_unresolved",
}


class CompletionPolicyError(ValueError):
    """Raised when COMPLETE would discard unfinished required work."""


def remaining_required_obligation_gaps(
    state: OrchestrationRunState,
) -> set[str]:
    """Return required obligation/output gaps with no completion evidence."""

    return _remaining_required_gaps(state, None)


def required_missing_output_keys(state: OrchestrationRunState) -> set[str]:
    """Return required output keys still missing from delegation outcomes."""

    required_output_keys = {
        output.output_key
        for intent in state.dispatch_intents
        for output in intent.expected_outputs
        if output.required and output.output_key
    }
    gaps: set[str] = set()
    for outcome in state.delegation_outcomes:
        gaps.update(set(outcome.missing_output_keys or []) & required_output_keys)
    return gaps


def successful_agent_outputs(state: OrchestrationRunState) -> list:
    return [
        output
        for output in state.agent_outputs
        if output.status in {"success", "completed", "fulfilled"}
        and (bool((output.text or "").strip()) or bool(output.artifact_keys))
    ]


def _obligation_matches_output_key(obligation: str, output_key: str) -> bool:
    return obligation == output_key or obligation.split(":$", 1)[0] == output_key


def _remaining_required_gaps(
    state: OrchestrationRunState,
    evidence: CompletionEvidence | None,
) -> set[str]:
    referenced_dispositions = (
        set(evidence.abandoned_goal_disposition_event_ids) if evidence else set()
    )
    disposed_families = {
        item.goal_family_fingerprint
        for item in state.goal_family_dispositions
        if item.event_id in referenced_dispositions
    }
    if evidence:
        disposed_families.update(
            item.goal_family_fingerprint
            for item in evidence.requested_goal_family_dispositions
            if item.event_id in referenced_dispositions
        )

    gaps: set[str] = set()
    covered_families: set[str] = set()
    for progress in state.goal_progress:
        covered_families.add(progress.goal_family_fingerprint)
        if progress.goal_family_fingerprint not in disposed_families:
            gaps.update(progress.remaining_required_obligations)

    required_output_keys = {
        output.output_key
        for intent in state.dispatch_intents
        for output in intent.expected_outputs
        if output.required and output.output_key
    }
    for outcome in state.delegation_outcomes:
        if outcome.goal_family_fingerprint in disposed_families:
            continue
        if outcome.goal_family_fingerprint not in covered_families:
            gaps.update(outcome.remaining_required_obligations)
        gaps.update(set(outcome.missing_output_keys) & required_output_keys)

    if evidence is None:
        return gaps
    satisfied = set(evidence.satisfied_output_keys)
    waived = {
        waiver.output_key for waiver in evidence.waived_outputs if waiver.reason.strip()
    }
    return {
        gap
        for gap in gaps
        if not any(
            _obligation_matches_output_key(gap, key) for key in satisfied | waived
        )
    }


def _validate_completion_gate(
    state: OrchestrationRunState,
    evidence: CompletionEvidence | None,
) -> None:
    blocking_failures = [
        failure
        for failure in state.open_failures
        if failure.status == "open"
        and failure.recoverable
        and failure.error_code not in _NON_BLOCKING_REFERENCE_FAILURE_CODES
    ]
    if blocking_failures:
        import logging

        _logger = logging.getLogger(__name__)
        for bf in blocking_failures:
            _logger.warning(
                "completion_gate_blocking_failure run_id=%s failure_id=%s "
                "source=%s error_code=%s fingerprint=%s agent_message_id=%s",
                state.run_id,
                bf.failure_id,
                bf.source,
                bf.error_code,
                bf.fingerprint,
                getattr(bf, "agent_message_id", None),
            )
    checks = (
        (
            any(
                dispatch.status not in TERMINAL_DISPATCH_STATUSES
                for dispatch in state.active_dispatches
            ),
            "complete rejected while active dispatches are pending",
        ),
        (
            any(
                item.status in {"open", "resuming"}
                for item in state.pending_agent_continuations
            ),
            "complete rejected while continuations are pending",
        ),
        (
            any(
                blocker.status == "open" and blocker.validation_status == "validated"
                for blocker in state.blockers
            ),
            "complete rejected by validated blocker",
        ),
        (
            bool(blocking_failures),
            "complete rejected by recoverable failure",
        ),
    )
    for rejected, message in checks:
        if rejected:
            raise CompletionPolicyError(message)
    if gaps := _remaining_required_gaps(state, evidence):
        raise CompletionPolicyError(
            "complete rejected by remaining required obligations: "
            + ", ".join(sorted(gaps))
        )


def determine_finalization_mode(
    state: OrchestrationRunState,
    action: PlannerActionType,
    *,
    completion_evidence: CompletionEvidence | None = None,
) -> FinalizationMode:
    """Return the only valid finalization mode for a terminal planner action."""

    if action == PlannerActionType.PLATFORM_ANSWER:
        return FinalizationMode.PLATFORM
    if action != PlannerActionType.COMPLETE:
        raise CompletionPolicyError("finalization requires COMPLETE or PLATFORM_ANSWER")

    _validate_completion_gate(state, completion_evidence)
    outputs = successful_agent_outputs(state)
    agent_ids = {output.agent_id for output in outputs}
    if not outputs and not state.facts:
        raise CompletionPolicyError("complete requires a valid completion basis")
    if len(agent_ids) >= 2:
        return FinalizationMode.SYNTHESIS
    if len(agent_ids) == 1:
        must_disclose_failure = any(
            failure.status == "open" and not failure.recoverable
            for failure in state.open_failures
        )
        return (
            FinalizationMode.PLATFORM
            if must_disclose_failure
            else FinalizationMode.DIRECT_AGENT
        )
    return FinalizationMode.PLATFORM


__all__ = [
    "CompletionPolicyError",
    "FinalizationMode",
    "determine_finalization_mode",
    "remaining_required_obligation_gaps",
    "required_missing_output_keys",
    "successful_agent_outputs",
]
