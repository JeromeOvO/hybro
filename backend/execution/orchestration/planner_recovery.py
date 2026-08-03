from __future__ import annotations

from uuid import uuid4

from common.utils.time import utcnow
from models.orchestration import (
    DispatchRefKind,
    OpenFailureRecord,
    OrchestrationRunState,
    PlannerAction,
)


def recovery_hints_for_planner_error(error_code: str) -> list[str]:
    if error_code == "delegate_blocked_pending_user":
        return ["ask_user_for_validated_blocker", "do_not_repeat_blocked_agent_goal"]
    if error_code == "delegate_repair_lineage_required":
        return [
            "use_backend_normalized_repair_lineage",
            "choose_alternate_agent_or_ask_user",
        ]
    if error_code == "delegate_no_progress_repeat":
        return [
            "ask_user_for_validated_blocker",
            "choose_alternate_agent",
            "fail_with_actionable_summary",
        ]
    if error_code == "delegate_resource_ref_omitted":
        return [
            "select_mentioned_resource_via_refs",
            "remove_resource_ids_and_facts_from_task",
        ]
    if error_code.startswith("ask_user_blocker"):
        return ["reference_validated_blocker_keys"]
    return ["replan_with_valid_schema", "choose_valid_refs"]


def planner_validation_fingerprint(
    *,
    error_code: str,
    stage: str,
    planner_action: PlannerAction | None,
) -> str:
    if planner_action is None:
        return f"planner_validator:{stage}:{error_code}:no_action"

    ref_kinds: set[str] = set()
    for target in planner_action.targets:
        for ref in (
            list(target.context_refs)
            + list(target.artifact_refs)
            + list(target.attachment_refs)
        ):
            ref_kinds.add(
                ref.kind.value
                if isinstance(ref.kind, DispatchRefKind)
                else str(ref.kind)
            )
    refs = ",".join(sorted(ref_kinds)) if ref_kinds else "none"
    return (
        f"planner_validator:{stage}:{error_code}:{planner_action.action.value}:"
        f"targets={len(planner_action.targets)}:refs={refs}"
    )


def record_recoverable_planner_rejection(
    state: OrchestrationRunState,
    *,
    error_code: str,
    error_message: str,
    planner_action: PlannerAction | None,
    stage: str,
    max_retries: int = 2,
) -> tuple[OpenFailureRecord, bool]:
    """Record a rejected planner attempt on a mutable run-state copy."""

    state.steps_used += 1
    fingerprint = planner_validation_fingerprint(
        error_code=error_code,
        stage=stage,
        planner_action=planner_action,
    )
    existing = next(
        (
            failure
            for failure in state.open_failures
            if failure.fingerprint == fingerprint and failure.status == "open"
        ),
        None,
    )
    if existing is None:
        failure = OpenFailureRecord(
            failure_id=uuid4().hex,
            fingerprint=fingerprint,
            source="planner_validator",
            error_code=error_code,
            error_message=error_message,
            recoverable=True,
            retry_count=0,
            max_retries=max_retries,
            status="open",
            recovery_hints=recovery_hints_for_planner_error(error_code),
            updated_at=utcnow(),
        )
        state.open_failures.append(failure)
        return failure, False

    existing.retry_count = min(existing.retry_count + 1, existing.max_retries)
    existing.error_message = error_message
    existing.updated_at = utcnow()
    exhausted = existing.retry_count >= existing.max_retries
    if exhausted:
        existing.status = "abandoned"
    return existing, exhausted


def resolve_open_planner_validation_failures(state: OrchestrationRunState) -> None:
    """Resolve schema failures once a later planner action is valid."""

    resolved_at = utcnow()
    for failure in state.open_failures:
        if failure.source != "planner_validator" or failure.status != "open":
            continue
        failure.status = "resolved"
        failure.updated_at = resolved_at
