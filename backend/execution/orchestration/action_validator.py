"""Validation for v2 planner actions."""

from __future__ import annotations

from collections.abc import Iterable

from models.orchestration import (
    OrchestrationRunState,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
)


class PlannerActionValidationError(ValueError):
    """Raised when a planner action is not valid for the current run state."""


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

        if steps_used >= step_budget and action.action not in (
            PlannerActionType.SYNTHESIZE,
            PlannerActionType.FAIL,
        ):
            raise PlannerActionValidationError(
                f"planner action {action.action.value!r} is not allowed after "
                "the step budget is exhausted"
            )

        if action.action == PlannerActionType.DELEGATE:
            if not action.targets:
                raise PlannerActionValidationError(
                    "delegate action requires at least one target"
                )

            candidate_ids = set(candidate_agent_ids)
            for target in action.targets:
                if target.agent_id not in candidate_ids:
                    raise PlannerActionValidationError(
                        f"delegate target {target.agent_id!r} is not in "
                        "candidate_agent_ids"
                    )
                if not target.task.strip():
                    raise PlannerActionValidationError(
                        f"delegate target {target.agent_id!r} requires a "
                        "non-empty task"
                    )
                if run_state is not None:
                    _validate_required_artifact_refs(target, run_state)

        if (
            action.action
            in (PlannerActionType.SYNTHESIZE, PlannerActionType.COMPLETE)
            and not has_agent_output
        ):
            raise PlannerActionValidationError(
                f"planner action {action.action.value!r} requires agent output"
            )

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
                "complete action requires completion evidence"
            )
        if run_state.pending_hitl_request_ids:
            raise PlannerActionValidationError(
                "complete action is blocked by pending HITL"
            )
        active = [
            item
            for item in run_state.active_dispatches
            if item.status not in {"completed", "failed", "canceled"}
        ]
        if active:
            raise PlannerActionValidationError(
                "complete action is blocked by active dispatches"
            )
        blocking_questions = [
            question
            for question in run_state.open_questions
            if not question.get("resolved") and question.get("blocking", True)
        ]
        if blocking_questions or evidence.unresolved_questions:
            raise PlannerActionValidationError(
                "complete action is blocked by unresolved questions"
            )
        if not run_state.agent_outputs and not run_state.facts:
            raise PlannerActionValidationError(
                "complete action requires agent output or facts"
            )

        fact_ids = {
            str(fact.get("fact_id"))
            for fact in run_state.facts
            if isinstance(fact, dict) and fact.get("fact_id") is not None
        }
        artifact_keys = {
            str(artifact.get("artifact_key"))
            for artifact in run_state.artifacts
            if isinstance(artifact, dict)
            and artifact.get("artifact_key") is not None
        }
        for fact_id in evidence.referenced_fact_ids:
            if fact_id not in fact_ids:
                raise PlannerActionValidationError(
                    f"complete action references unknown fact {fact_id!r}"
                )
        for artifact_key in evidence.referenced_artifact_keys:
            if artifact_key not in artifact_keys:
                raise PlannerActionValidationError(
                    f"complete action references unknown artifact {artifact_key!r}"
                )
        if not evidence.satisfied_criteria:
            raise PlannerActionValidationError(
                "complete action requires satisfied criteria"
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
                f"unknown artifact {ref.ref_id!r}"
            )
