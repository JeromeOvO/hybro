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
        candidate_agent_ids: Iterable[str] = (),
        steps_used: int = 0,
        step_budget: int = 8,
        has_agent_output: bool = False,
        run_state: OrchestrationRunState | None = None,
    ) -> PlannerAction:
        """Return ``action`` unchanged when it is valid for the run state."""

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

        return action


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
