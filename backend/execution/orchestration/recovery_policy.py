from __future__ import annotations

import re
from uuid import uuid4

from execution.orchestration.blocker_matching import (
    agent_blocker_field_key,
    match_tokens,
    normalize_match_text,
)
from execution.orchestration.completion_policy import required_missing_output_keys
from execution.orchestration.context_ref_resolution import (
    rewrite_context_refs_with_available_facts,
)
from execution.orchestration.goal_fingerprinting import target_goal_fingerprints
from execution.orchestration.outcome_policy import OutcomeHistoryView
from models.orchestration import (
    BlockerRecord,
    DelegationOutcomeRecord,
    DispatchExpectedOutput,
    OrchestrationRunState,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
    PlannerQuestion,
)

_ENFORCEABLE_EXPECTED_OUTPUT_KIND = "artifact"
_JSON_MACHINE_SEGMENT = r"(?:[a-z0-9_$][^\s.]*|[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+)"
_ENFORCEABLE_JSON_FIELD_PATH = re.compile(
    rf"^{_JSON_MACHINE_SEGMENT}(?:\.{_JSON_MACHINE_SEGMENT})*$"
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


def _open_validated_user_blockers(
    state: OrchestrationRunState,
) -> list[BlockerRecord]:
    return [
        blocker
        for blocker in state.blockers
        if blocker.status == "open"
        and blocker.validation_status == "validated"
        and blocker.validated_user_only
    ]


def _ask_user_action_for_validated_blockers(
    state: OrchestrationRunState,
    *,
    reasoning: str,
) -> PlannerAction | None:
    validated_blockers = _open_validated_user_blockers(state)
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
        reasoning=reasoning,
        questions=[
            PlannerQuestion(
                prompt="\n".join(
                    _question_prompt_for_blocker(state, blocker)
                    for blocker in sorted_blockers
                ),
                reason="blocker",
                blocker_keys=blocker_keys,
                required_obligation_keys=required_obligation_keys,
                blocker_obligations=blocker_obligations,
            )
        ],
    )


def _question_prompt_for_blocker(
    state: OrchestrationRunState,
    blocker: BlockerRecord,
) -> str:
    """Prefer the agent's actual private question text for input-required
    blockers so the generated questionnaire reads the real question."""
    if blocker.key.endswith(":agent_input_required"):
        evidence_refs = set(blocker.evidence_refs)
        for observation in state.private_agent_input_observations:
            raw_prompt = getattr(observation, "raw_prompt", None)
            if not isinstance(raw_prompt, str) or not raw_prompt.strip():
                continue
            if (
                observation.agent_message_id in evidence_refs
                or f"{observation.agent_message_id}:awaiting_input" in evidence_refs
            ):
                return raw_prompt.strip()
    return blocker.description


def _has_completable_agent_progress(state: OrchestrationRunState) -> bool:
    """True when fulfilled Agent work remains and no required gaps are open."""
    if not any(outcome.status == "fulfilled" for outcome in state.delegation_outcomes):
        return False
    if required_missing_output_keys(state):
        return False
    if state.goal_progress:
        return not any(
            progress.remaining_required_obligations for progress in state.goal_progress
        )
    # Prefer goal_progress when present. Without it, ignore remaining lists on
    # already-fulfilled outcomes (common in older fixtures) and only block when
    # a non-fulfilled outcome still declares required gaps.
    return not any(
        outcome.remaining_required_obligations
        for outcome in state.delegation_outcomes
        if outcome.status != "fulfilled"
    )


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
    return _ask_user_action_for_validated_blockers(
        state,
        reasoning=(
            "Backend recovery selected HITL because validated user-only blockers "
            "are open."
        ),
    )


def action_for_rejected_ask_user(
    state: OrchestrationRunState,
    *,
    error_code: str,
) -> PlannerAction | None:
    """Recover from illegal post-dispatch ask_user planner actions.

    After Agents have already run, ask_user is only legal for validated
    blockers. When the planner invents clarifying questions instead, prefer a
    corrected HITL action when validated blockers exist; otherwise complete when
    Agent results already satisfy the goal so the run does not exhaust retries
    while stuck on ``checking_goal``.
    """

    if not error_code.startswith("ask_user_blocker"):
        return None
    ask_user = _ask_user_action_for_validated_blockers(
        state,
        reasoning=(
            "Backend recovery selected HITL because validated user-only blockers "
            "are open."
        ),
    )
    if ask_user is not None:
        return ask_user
    return _complete_when_agent_results_fulfilled(
        state,
        reasoning=(
            "Backend recovery selected complete because Agent results already "
            "satisfy the goal and ask_user had no validated blocker."
        ),
    )


_TERMINAL_INTENT_RECOVERY_CODES = frozenset(
    {
        # Planner already tried to end the turn; empty-evidence complete is safe.
        "completion_evidence_invalid",
        "platform_answer_instruction_missing",
        "fail_goal_already_satisfied",
    }
)
_EXHAUSTED_ONLY_RECOVERY_CODES = frozenset(
    {
        # Planner wanted more work; completing early can skip remaining agents.
        "delegate_goal_already_fulfilled",
    }
)
_FULFILLED_GOAL_RECOVERY_CODES = (
    _TERMINAL_INTENT_RECOVERY_CODES | _EXHAUSTED_ONLY_RECOVERY_CODES
)


def action_for_fulfilled_goal_recovery(
    state: OrchestrationRunState,
    *,
    error_code: str,
    exhausted: bool = False,
) -> PlannerAction | None:
    """Recover when Agents already fulfilled but the planner cannot terminate.

    After successful Agent work, the planner may invent invalid completion
    evidence (e.g. ``:text`` facts for artifact-only Agents), re-delegate an
    already fulfilled goal, or emit ``platform_answer`` without a synthesis
    instruction. Prefer ``complete`` with empty evidence so Execution can
    synthesize instead of exhausting retries into ``unable_to_continue``.

    ``delegate_goal_already_fulfilled`` only recovers when ``exhausted`` is true,
    so a premature re-delegate cannot finalize the run before other Agents run.
    Termination-intent codes may recover earlier because the planner was already
    trying to end the turn.
    """

    if error_code in _EXHAUSTED_ONLY_RECOVERY_CODES and not exhausted:
        return None
    if error_code not in _FULFILLED_GOAL_RECOVERY_CODES:
        return None
    ask_user = _ask_user_action_for_validated_blockers(
        state,
        reasoning=(
            "Backend recovery selected HITL because validated user-only blockers "
            "are open."
        ),
    )
    if ask_user is not None:
        return ask_user
    return _complete_when_agent_results_fulfilled(
        state,
        reasoning=(
            "Backend recovery selected complete because Agent results already "
            "satisfy the goal and the planner could not terminate cleanly."
        ),
    )


def _complete_when_agent_results_fulfilled(
    state: OrchestrationRunState,
    *,
    reasoning: str,
) -> PlannerAction | None:
    if not _has_completable_agent_progress(state):
        return None
    return PlannerAction(
        action=PlannerActionType.COMPLETE,
        reasoning=reasoning,
        completion_evidence=None,
    )


def _required_obligations_for_blocker(
    state: OrchestrationRunState,
    blocker: BlockerRecord,
) -> list[str]:
    blocked_outputs = set(blocker.blocked_output_keys)
    for outcome in reversed(state.delegation_outcomes):
        candidates = [
            obligation
            for obligation in outcome.remaining_required_obligations
            if not blocked_outputs or obligation.partition(":")[0] in blocked_outputs
        ]
        if not candidates:
            continue
        blocker_field_key = agent_blocker_field_key(blocker.key)
        if blocker_field_key is None:
            return sorted(dict.fromkeys(candidates))
        obligations = [
            obligation
            for obligation in candidates
            if _obligation_matches_blocker_field(
                blocker_field_key,
                obligation,
            )
        ]
        if obligations:
            return sorted(dict.fromkeys(obligations))
    return []


def _obligation_matches_blocker_field(
    blocker_field_key: str,
    obligation: str,
) -> bool:
    output_key, separator, field_key = obligation.partition(":")
    if not separator:
        return False
    if field_key == "$present":
        return normalize_match_text(blocker_field_key) == normalize_match_text(
            output_key
        )
    field_tokens = match_tokens(field_key)
    return bool(field_tokens) and field_tokens <= match_tokens(blocker_field_key)


def normalize_independent_parallel_group(action: PlannerAction) -> PlannerAction:
    """Fill a shared parallel_group for independent multi-target delegates.

    When the planner returns multiple independent targets without a usable shared
    parallel_group (all omitted/blank, or only one non-blank group among nulls),
    Execution owns the fanout grouping so the run does not fail on a recoverable
    schema omission. Conflicting non-empty groups are left for the validator.
    """

    if action.action != PlannerActionType.DELEGATE or len(action.targets) <= 1:
        return action
    if any(target.depends_on for target in action.targets):
        return action

    groups = {
        (target.parallel_group or "").strip() or None for target in action.targets
    }
    non_null_groups = {group for group in groups if group is not None}
    if len(non_null_groups) > 1:
        return action
    if len(non_null_groups) == 1 and None not in groups:
        return action

    group_id = next(iter(non_null_groups), f"fanout-{uuid4().hex[:8]}")
    targets = [
        target.model_copy(update={"parallel_group": group_id}, deep=True)
        for target in action.targets
    ]
    return action.model_copy(update={"targets": targets}, deep=True)


def normalize_context_refs_with_available_facts(
    action: PlannerAction,
    state: OrchestrationRunState,
) -> PlannerAction:
    """Rewrite symbolic context_refs onto durable fact IDs before validation."""

    if action.action != PlannerActionType.DELEGATE:
        return action
    targets: list[PlannedDelegateTarget] = []
    changed = False
    for target in action.targets:
        original = list(target.context_refs)
        rewritten = rewrite_context_refs_with_available_facts(state, original)
        if rewritten is original:
            targets.append(target)
            continue
        changed = True
        targets.append(target.model_copy(update={"context_refs": rewritten}, deep=True))
    if not changed:
        return action
    return action.model_copy(update={"targets": targets}, deep=True)


def _is_enforceable_expected_output(output: DispatchExpectedOutput) -> bool:
    """Return whether Execution can score the declared output contract."""

    kind = (output.kind or "").strip().lower()
    return (
        kind
        in {
            _ENFORCEABLE_EXPECTED_OUTPUT_KIND,
            "file",
            "image",
            "audio",
            "video",
            "text",
            "markdown",
        }
        or kind.startswith("text/")
        or "/" in kind
    )


def normalize_prose_expected_outputs(action: PlannerAction) -> PlannerAction:
    """Drop invented contracts while preserving enforceable text and media outputs."""

    if action.action != PlannerActionType.DELEGATE:
        return action

    targets: list[PlannedDelegateTarget] = []
    changed = False
    for target in action.targets:
        kept: list[DispatchExpectedOutput] = []
        for output in target.expected_outputs:
            if not _is_enforceable_expected_output(output):
                changed = True
                continue
            kind = (output.kind or "").strip().lower()
            normalized = output
            if kind in {"text", "markdown"} or kind.startswith("text/"):
                normalized = output.model_copy(
                    update={"artifact_name": None, "required_fields": []},
                    deep=True,
                )
            elif kind == "application/json":
                normalized = output.model_copy(
                    update={
                        "required_fields": [
                            path
                            for path in output.required_fields
                            if _ENFORCEABLE_JSON_FIELD_PATH.fullmatch(path)
                        ]
                    },
                    deep=True,
                )
            changed = changed or normalized != output
            kept.append(normalized)
        if kept == target.expected_outputs:
            targets.append(target)
            continue
        targets.append(target.model_copy(update={"expected_outputs": kept}, deep=True))
    if not changed:
        return action
    return action.model_copy(update={"targets": targets}, deep=True)


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
    if latest.status == "blocked" and _blocked_outcome_has_open_blocker(state, latest):
        return target
    if latest.status not in {"partial", "blocked", "no_progress"}:
        return target
    return target.model_copy(
        update={"repair_of_intent_id": latest.dispatch_intent_id},
        deep=True,
    )


def _blocked_outcome_has_open_blocker(
    state: OrchestrationRunState,
    outcome: DelegationOutcomeRecord,
) -> bool:
    if not outcome.blockers:
        return True
    current_by_key = {blocker.key: blocker for blocker in state.blockers}
    for historical in outcome.blockers:
        current = current_by_key.get(historical.key, historical)
        if current.status == "open":
            return True
    return False
