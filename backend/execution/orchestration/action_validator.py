"""Validation for v2 planner actions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from execution.orchestration.outcome_evaluator import (
    effective_output_key,
    goal_fingerprints,
)
from execution.orchestration.outcome_policy import (
    duplicate_delegate_target_code,
    evaluate_retry,
)
from models.orchestration import (
    CompletionEvidence,
    OrchestrationRunState,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
)

_NON_BLOCKING_REFERENCE_FAILURE_CODES = frozenset(
    {
        "attachment_ref_not_found",
        "context_ref_not_found",
        "artifact_ref_not_found",
        "dispatch_payload_ref_unresolved",
    }
)


class PlannerActionValidationError(ValueError):
    """Raised when a planner action is not valid for the current run state."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "planner_action_invalid",
        recoverable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.recoverable = recoverable


class PlannerActionValidator:
    """Validate v2 planner actions against runtime orchestration constraints."""

    @staticmethod
    def validate(
        action: PlannerAction,
        *,
        run_state: OrchestrationRunState | None = None,
        candidate_agent_ids: Iterable[str] = (),
        steps_used: int = 0,
        step_budget: int = 8,
        has_agent_output: bool = False,
        resource_fingerprints: Mapping[str, str] | None = None,
        guardrails_enabled: bool = False,
    ) -> PlannerAction:
        """Return ``action`` unchanged when it is valid for the run state."""

        if run_state is not None:
            candidate_agent_ids = (
                run_state.candidate_scope.agent_ids
                if run_state.candidate_scope is not None
                else run_state.candidate_agent_ids
            )
            steps_used = run_state.steps_used
            step_budget = run_state.step_budget
            has_agent_output = bool(run_state.agent_outputs)
            if action.action == PlannerActionType.COMPLETE:
                has_agent_output = bool(run_state.agent_outputs or run_state.facts)

        _validate_step_budget(action, steps_used=steps_used, step_budget=step_budget)
        if action.action == PlannerActionType.DELEGATE:
            _validate_delegate(
                action,
                candidate_agent_ids=candidate_agent_ids,
                run_state=run_state,
            )
            PlannerActionValidator._validate_delegate_outcome_policy(
                action,
                run_state=run_state,
                resource_fingerprints=resource_fingerprints or {},
                guardrails_enabled=guardrails_enabled,
            )
        _validate_terminal_output(action, has_agent_output=has_agent_output)
        if (
            action.action == PlannerActionType.ASK_USER
            and run_state is not None
        ):
            PlannerActionValidator._validate_ask_user(
                action,
                run_state,
                guardrails_enabled=guardrails_enabled,
            )

        if (
            run_state is not None
            and action.action
            in (PlannerActionType.SYNTHESIZE, PlannerActionType.COMPLETE)
        ):
            _validate_no_blocking_recoverable_failures(action, run_state)
        if action.action == PlannerActionType.COMPLETE and run_state is not None:
            PlannerActionValidator._validate_completion(action, run_state)

        return action

    @staticmethod
    def _validate_completion(
        action: PlannerAction,
        run_state: OrchestrationRunState,
    ) -> None:
        evidence = action.completion_evidence
        if evidence is None:
            raise PlannerActionValidationError(
                "complete action requires completion evidence",
                code="completion_evidence_invalid",
            )
        _validate_completion_blockers(run_state, evidence)
        _validate_completion_references(run_state, evidence)
        if not evidence.satisfied_criteria or any(
            not criterion.strip() for criterion in evidence.satisfied_criteria
        ):
            raise PlannerActionValidationError(
                "complete action requires satisfied criteria",
                code="completion_evidence_invalid",
            )

    @staticmethod
    def _validate_delegate_outcome_policy(
        action: PlannerAction,
        *,
        run_state: OrchestrationRunState | None,
        resource_fingerprints: Mapping[str, str],
        guardrails_enabled: bool,
    ) -> None:
        if run_state is None or not guardrails_enabled:
            return

        target_fingerprints = [
            PlannerActionValidator._target_goal_fingerprints(
                target,
                resource_fingerprints,
            )
            for target in action.targets
        ]
        duplicate_code = duplicate_delegate_target_code(
            action.targets,
            [item.goal_family_fingerprint for item in target_fingerprints],
        )
        decisions = [
            duplicate_code,
            *[
                evaluate_retry(
                    run_state,
                    target,
                    fingerprints.goal_family_fingerprint,
                    fingerprints.goal_revision_fingerprint,
                ).code
                for target, fingerprints in zip(
                    action.targets,
                    target_fingerprints,
                    strict=True,
                )
            ],
        ]
        if code := next((code for code in decisions if code is not None), None):
            raise PlannerActionValidationError(
                f"delegate action violates outcome policy: {code}",
                code=code,
            )

    @staticmethod
    def _target_goal_fingerprints(
        target: PlannedDelegateTarget,
        resource_fingerprints: Mapping[str, str],
    ):
        selected_content_fingerprints = [
            resource_fingerprints[ref.ref_id]
            for ref in (
                *target.context_refs,
                *target.artifact_refs,
                *target.attachment_refs,
            )
            if ref.ref_id in resource_fingerprints
        ]
        return goal_fingerprints(
            agent_id=target.agent_id,
            expected_outputs=list(target.expected_outputs),
            selected_content_fingerprints=selected_content_fingerprints,
            dependency_family_fingerprints=[],
            upstream_output_fingerprints=[],
        )

    @staticmethod
    def _validate_ask_user(
        action: PlannerAction,
        run_state: OrchestrationRunState,
        *,
        guardrails_enabled: bool,
    ) -> None:
        if not guardrails_enabled:
            return

        question_prompts = {
            normalized
            for question in action.questions
            if (normalized := PlannerActionValidator._normalize_question_prompt(question.prompt))
        }
        if not question_prompts:
            if run_state.dispatch_intents:
                PlannerActionValidator._validate_post_dispatch_ask_user(
                    action,
                    run_state,
                )
            return

        resolved_prompts = {
            normalized
            for question in run_state.open_questions
            if isinstance(question, dict)
            and question.get("source") == "supervisor"
            and question.get("resolved") is True
            and question.get("status") == "resolved"
            and (
                normalized := PlannerActionValidator._normalize_question_prompt(
                    question.get("prompt")
                )
            )
        }
        if question_prompts & resolved_prompts:
            raise PlannerActionValidationError(
                "ask_user action repeats already answered supervisor question(s)",
                code="duplicate_answered_question",
            )

        pending_prompts = {
            normalized
            for question in run_state.open_questions
            if isinstance(question, dict)
            and question.get("source") == "supervisor"
            and question.get("status") in {"creating", "open", "pending"}
            and (
                normalized := PlannerActionValidator._normalize_question_prompt(
                    question.get("prompt")
                )
            )
        }
        if question_prompts & pending_prompts:
            raise PlannerActionValidationError(
                "ask_user action repeats pending supervisor question(s)",
                code="duplicate_pending_question",
            )

        if not run_state.dispatch_intents:
            return

        PlannerActionValidator._validate_post_dispatch_ask_user(action, run_state)

    @staticmethod
    def _validate_post_dispatch_ask_user(
        action: PlannerAction,
        run_state: OrchestrationRunState,
    ) -> None:
        if not action.questions:
            raise PlannerActionValidationError(
                "post-dispatch ask_user action requires blocker keys",
                code="ask_user_blocker_keys_required",
            )

        required_output_keys = {
            effective_output_key(output)
            for intent in run_state.dispatch_intents
            for output in intent.expected_outputs
            if output.required
        }
        blockers_by_key = {blocker.key: blocker for blocker in run_state.blockers}
        seen_prompts: set[str] = set()
        seen_blocker_keys: set[str] = set()
        for question in action.questions:
            prompt = PlannerActionValidator._normalize_question_prompt(question.prompt)
            blocker_keys = set(question.blocker_keys)
            if (
                not prompt
                or question.reason != "blocker"
                or not question.blocker_keys
            ):
                raise PlannerActionValidationError(
                    "post-dispatch ask_user action requires blocker keys",
                    code="ask_user_blocker_keys_required",
                )
            if prompt in seen_prompts or blocker_keys & seen_blocker_keys:
                raise PlannerActionValidationError(
                    "ask_user action repeats a question or blocker in the same action",
                    code="duplicate_question_in_action",
                )
            seen_prompts.add(prompt)
            seen_blocker_keys.update(blocker_keys)
            for blocker_key in blocker_keys:
                blocker = blockers_by_key.get(blocker_key)
                if (
                    blocker is None
                    or blocker.status != "open"
                    or not blocker.claimed_user_only
                    or not blocker.validated_user_only
                    or blocker.validation_status != "validated"
                    or not (
                        set(blocker.blocked_output_keys) & required_output_keys
                    )
                ):
                    raise PlannerActionValidationError(
                        "ask_user action references a non-validated blocker",
                        code="ask_user_blocker_not_validated",
                    )
            previously_asked = [
                item
                for item in run_state.open_questions
                if isinstance(item, dict)
                and item.get("source") == "supervisor"
                and set(item.get("blocker_keys") or [])
                & set(question.blocker_keys)
            ]
            if any(item.get("status") == "resolved" for item in previously_asked):
                raise PlannerActionValidationError(
                    "ask_user action repeats answered blocker question(s)",
                    code="duplicate_answered_question",
                )
            if any(
                item.get("status") in {"creating", "open", "pending"}
                for item in previously_asked
            ):
                raise PlannerActionValidationError(
                    "ask_user action repeats pending blocker question(s)",
                    code="duplicate_pending_question",
                )

    @staticmethod
    def _normalize_question_prompt(prompt: object) -> str:
        if not isinstance(prompt, str):
            return ""
        return " ".join(prompt.lower().split())

def _validate_step_budget(
    action: PlannerAction,
    *,
    steps_used: int,
    step_budget: int,
) -> None:
    if steps_used >= step_budget and action.action not in (
        PlannerActionType.SYNTHESIZE,
        PlannerActionType.FAIL,
    ):
        raise PlannerActionValidationError(
            f"planner action {action.action.value!r} is not allowed after "
            "the step budget is exhausted",
            code="step_budget_exhausted",
            recoverable=False,
        )


def _validate_delegate(
    action: PlannerAction,
    *,
    candidate_agent_ids: Iterable[str],
    run_state: OrchestrationRunState | None,
) -> None:
    if not action.targets:
        raise PlannerActionValidationError(
            "delegate action requires at least one target",
            code="delegate_target_missing",
        )
    if len(action.targets) > 1:
        parallel_groups = {target.parallel_group for target in action.targets}
        has_single_group = len(parallel_groups) == 1 and all(
            isinstance(group, str) and bool(group.strip())
            for group in parallel_groups
        )
        has_intra_action_dependency = any(
            target.depends_on for target in action.targets
        )
        if not has_single_group or has_intra_action_dependency:
            raise PlannerActionValidationError(
                "multi-target delegate requires one explicit independent "
                "parallel_group",
                code="parallel_dependency_unspecified",
            )
    candidate_ids = set(candidate_agent_ids)
    for target in action.targets:
        if target.agent_id not in candidate_ids:
            raise PlannerActionValidationError(
                f"delegate target {target.agent_id!r} is not in candidate_agent_ids",
                code="target_out_of_scope",
            )
        if not target.task.strip():
            raise PlannerActionValidationError(
                f"delegate target {target.agent_id!r} requires a non-empty task",
                code="delegate_task_empty",
            )
        if run_state is not None:
            _validate_required_artifact_refs(target, run_state)


def _validate_terminal_output(
    action: PlannerAction,
    *,
    has_agent_output: bool,
) -> None:
    if action.action in (
        PlannerActionType.SYNTHESIZE,
        PlannerActionType.COMPLETE,
    ) and not has_agent_output:
        raise PlannerActionValidationError(
            f"planner action {action.action.value!r} requires agent output"
        )


def _validate_completion_blockers(
    run_state: OrchestrationRunState,
    evidence: CompletionEvidence,
) -> None:
    if run_state.pending_hitl_request_ids:
        raise PlannerActionValidationError(
            "complete action is blocked by pending HITL",
            code="completion_evidence_invalid",
        )
    if any(
        item.status not in {"completed", "failed", "canceled", "rejected"}
        for item in run_state.active_dispatches
    ):
        raise PlannerActionValidationError(
            "complete action is blocked by active dispatches",
            code="completion_evidence_invalid",
        )
    has_unresolved_question = any(
        not isinstance(question, Mapping)
        or (
            question.get("status") != "resolved"
            and question.get("resolved") is not True
        )
        for question in run_state.open_questions
    )
    if has_unresolved_question or evidence.unresolved_questions:
        raise PlannerActionValidationError(
            "complete action is blocked by unresolved questions",
            code="completion_evidence_invalid",
        )
    if not run_state.agent_outputs and not run_state.facts:
        raise PlannerActionValidationError(
            "complete action requires agent output or facts",
            code="completion_evidence_invalid",
        )


def _validate_no_blocking_recoverable_failures(
    action: PlannerAction,
    run_state: OrchestrationRunState,
) -> None:
    if any(
        failure.recoverable
        and failure.source != "planner_validator"
        and failure.status == "open"
        and failure.error_code not in _NON_BLOCKING_REFERENCE_FAILURE_CODES
        for failure in run_state.open_failures
    ):
        if action.action == PlannerActionType.COMPLETE:
            raise PlannerActionValidationError(
                "complete action is blocked by open recoverable failure",
                code="completion_blocked_by_recoverable_failure",
            )
        raise PlannerActionValidationError(
            f"{action.action.value} action is blocked by open recoverable failure",
            code="completion_blocked_by_recoverable_failure",
        )


def _validate_completion_references(
    run_state: OrchestrationRunState,
    evidence: CompletionEvidence,
) -> None:
    fact_ids = {
        str(fact.get("fact_id"))
        for fact in run_state.facts
        if isinstance(fact, dict) and fact.get("fact_id") is not None
    }
    artifact_keys = {
        str(artifact.get("artifact_key"))
        for artifact in run_state.artifacts
        if isinstance(artifact, dict) and artifact.get("artifact_key") is not None
    }
    for fact_id in evidence.referenced_fact_ids:
        if fact_id not in fact_ids:
            raise PlannerActionValidationError(
                f"complete action references unknown fact {fact_id!r}",
                code="completion_evidence_invalid",
            )
    for artifact_key in evidence.referenced_artifact_keys:
        if artifact_key not in artifact_keys:
            raise PlannerActionValidationError(
                f"complete action references unknown artifact {artifact_key!r}",
                code="completion_evidence_invalid",
            )


def _validate_required_artifact_refs(
    target: PlannedDelegateTarget,
    run_state: OrchestrationRunState,
) -> None:
    artifact_keys = {
        str(artifact.get("artifact_key"))
        for artifact in run_state.artifacts
        if isinstance(artifact, dict) and artifact.get("artifact_key") is not None
    }
    for ref in target.artifact_refs:
        if ref.required and ref.ref_id not in artifact_keys:
            raise PlannerActionValidationError(
                f"delegate target {target.agent_id!r} references "
                f"unknown artifact {ref.ref_id!r}",
                code="artifact_ref_not_found",
            )
