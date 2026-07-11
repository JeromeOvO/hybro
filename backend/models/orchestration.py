from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from common.utils.time import utcnow


class OrchestrationStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    DISPATCHING = "dispatching"
    WAITING_AGENT = "waiting_agent"
    INGESTING = "ingesting"
    AWAITING_USER = "awaiting_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    BUDGET_EXHAUSTED = "budget_exhausted"


TERMINAL_ORCHESTRATION_STATUSES = {
    OrchestrationStatus.COMPLETED,
    OrchestrationStatus.FAILED,
    OrchestrationStatus.CANCELED,
    OrchestrationStatus.BUDGET_EXHAUSTED,
}


class OrchestrationEventType(StrEnum):
    RUN_CREATED = "run_created"
    PLANNER_CONTEXT_BUILT = "planner_context_built"
    PLANNER_ACTION_PROPOSED = "planner_action_proposed"
    PLANNER_ACTION_REJECTED = "planner_action_rejected"
    DISPATCH_INTENT_RECORDED = "dispatch_intent_recorded"
    AGENT_DISPATCH_STARTED = "agent_dispatch_started"
    AGENT_DISPATCH_COMPLETED = "agent_dispatch_completed"
    AGENT_RESULT_INGESTED = "agent_result_ingested"
    STATE_REDUCED = "state_reduced"
    HITL_REQUESTED = "hitl_requested"
    HITL_RESOLVED = "hitl_resolved"
    RUN_TERMINAL = "run_terminal"
    RUN_RECOVERED = "run_recovered"
    PUBLIC_LIFECYCLE_PROJECTED = "public_lifecycle_projected"
    OUTCOME_EVALUATED = "outcome_evaluated"
    REQUIRED_EVIDENCE_INVALIDATED = "required_evidence_invalidated"
    CONTINUATION_CLAIMED = "continuation_claimed"
    CONTINUATION_RESOLVED = "continuation_resolved"
    CONTINUATION_ABANDONED = "continuation_abandoned"
    GOAL_FAMILY_DISPOSED = "goal_family_disposed"


class PlannerActionType(StrEnum):
    DELEGATE = "delegate"
    ASK_USER = "ask_user"
    SYNTHESIZE = "synthesize"
    COMPLETE = "complete"
    FAIL = "fail"


class DispatchRefKind(StrEnum):
    CONTEXT = "context"
    ARTIFACT = "artifact"
    ATTACHMENT = "attachment"


class DispatchContentRef(BaseModel):
    kind: DispatchRefKind
    ref_id: str
    source_agent_message_id: str | None = None
    mime_type: str | None = None
    required: bool = True


class DispatchExpectedOutput(BaseModel):
    output_key: str | None = None
    kind: str
    required: bool = True
    description: str | None = None
    artifact_name: str | None = None
    required_fields: list[str] = Field(default_factory=list)
    allow_partial: bool = True

    @model_validator(mode="after")
    def _ensure_stable_output_key(self) -> DispatchExpectedOutput:
        if self.output_key is not None and self.output_key.strip():
            self.output_key = self.output_key.strip()
            return self

        contract = {
            "kind": " ".join(self.kind.split()),
            "artifact_name": (
                " ".join(self.artifact_name.split())
                if self.artifact_name is not None
                else None
            ),
            "required_fields": sorted(
                " ".join(field.split()) for field in self.required_fields
            ),
            "description": (
                " ".join(self.description.split())
                if self.description is not None
                else None
            ),
        }
        payload = json.dumps(
            contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self.output_key = f"legacy:{digest[:20]}"
        return self


class AssumptionRecord(BaseModel):
    key: str
    description: str
    source_agent_message_id: str | None = None
    applies_to_output_keys: list[str] = Field(default_factory=list)


class UnknownRecord(BaseModel):
    key: str
    description: str
    source_agent_message_id: str | None = None
    applies_to_output_keys: list[str] = Field(default_factory=list)


class BlockerResolutionAttempt(BaseModel):
    kind: Literal["resource", "agent", "conditional_result"]
    reference_id: str
    outcome: Literal["unavailable", "insufficient", "failed", "resolved"]
    applies_to_output_keys: list[str] = Field(default_factory=list)


class BlockerRecord(BaseModel):
    key: str
    description: str
    blocked_output_keys: list[str] = Field(default_factory=list)
    source: Literal["agent", "planner", "executor"]
    evidence_refs: list[str] = Field(default_factory=list)
    claimed_user_only: bool = False
    validation_status: Literal["candidate", "validated"] = Field(
        default="candidate",
        description=(
            "Authoritative user-only validation state. The validated_user_only "
            "compatibility field is derived from this value."
        ),
    )
    status: Literal["open", "resolved", "waived"] = "open"
    resolution_attempts: list[BlockerResolutionAttempt] = Field(default_factory=list)

    @computed_field(
        description="Compatibility mirror derived from validation_status.",
        return_type=bool,
    )
    @property
    def validated_user_only(self) -> bool:
        return self.validation_status == "validated"


class OpenFailureRecord(BaseModel):
    failure_id: str
    fingerprint: str
    source: Literal["a2a_adapter", "runtime", "executor", "planner_validator"]
    agent_id: str | None = None
    agent_message_id: str | None = None
    dispatch_intent_id: str | None = None
    error_code: str
    error_message: str
    recoverable: bool
    retry_count: int = 0
    max_retries: int = 2
    status: Literal["open", "resolved", "abandoned"] = "open"
    recovery_hints: list[str] = Field(default_factory=list)
    resolved_by_agent_message_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _retry_count_within_budget(self) -> OpenFailureRecord:
        if self.retry_count < 0 or self.retry_count > self.max_retries:
            raise ValueError("retry_count must be between 0 and max_retries")
        return self


class PlannedDelegateTarget(BaseModel):
    agent_id: str
    task: str
    agent_name: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    parallel_group: str | None = None
    required_resource_refs: list[str] = Field(default_factory=list)
    context_refs: list[DispatchContentRef] = Field(default_factory=list)
    artifact_refs: list[DispatchContentRef] = Field(default_factory=list)
    attachment_refs: list[DispatchContentRef] = Field(default_factory=list)
    expected_outputs: list[DispatchExpectedOutput] = Field(default_factory=list)
    repair_of_intent_id: str | None = None
    attachment_policy: Literal["explicit_refs_only", "compatible_only"] = (
        "explicit_refs_only"
    )


class PlannerQuestion(BaseModel):
    prompt: str
    prompt_type: Literal["text", "choice", "confirmation"] = "text"
    choices: list[str] | None = None
    reason: Literal["initial_clarification", "blocker"] = "initial_clarification"
    blocker_keys: list[str] = Field(default_factory=list)


class AuthorizationBasis(BaseModel):
    kind: Literal["room_member", "saved_group_member", "explicit_selection", "mention"]
    room_id: str | None = None
    group_id: str | None = None
    selected_by_user_id: str | None = None
    checked_at: datetime = Field(default_factory=utcnow)


class CandidateAgentSnapshot(BaseModel):
    agent_id: str
    name: str | None = None
    role: str | None = None
    capability_summary: str = ""
    status: str | None = None
    source: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=lambda: ["text"])
    output_modes: list[str] = Field(default_factory=list)
    supports_file_upload: bool = False
    success_rate: float | None = None


class CandidateScopeSnapshot(BaseModel):
    snapshot_id: str
    revision: int = 1
    source: str
    room_id: str
    group_id: str | None = None
    agent_ids: list[str]
    agents: list[CandidateAgentSnapshot] = Field(default_factory=list)
    room_membership_version: str | None = None
    group_version: str | None = None
    resolved_at: datetime = Field(default_factory=utcnow)
    authorization_basis: AuthorizationBasis | None = None

    @field_validator("revision")
    @classmethod
    def _revision_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("revision must be at least 1")
        return value


class ParticipantSnapshot(BaseModel):
    mode: Literal["direct", "sequential", "debate"]
    ordered_agent_ids: list[str]
    current_round: int = 0
    max_rounds: int | None = None
    turn_policy: Literal["all_once", "sequential_rounds", "debate_rounds"] = "all_once"
    completed_agent_ids: list[str] = Field(default_factory=list)


class WaivedOutputEvidence(BaseModel):
    output_key: str
    reason: str
    blocker_keys: list[str] = Field(default_factory=list)


class CompletionEvidence(BaseModel):
    satisfied_criteria: list[str] = Field(default_factory=list)
    referenced_fact_ids: list[str] = Field(default_factory=list)
    referenced_artifact_keys: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    final_answer_intent: str
    confidence: float
    satisfied_output_keys: list[str] = Field(default_factory=list)
    waived_outputs: list[WaivedOutputEvidence] = Field(default_factory=list)
    abandoned_goal_disposition_event_ids: list[str] = Field(default_factory=list)
    assumption_keys: list[str] = Field(default_factory=list)
    unresolved_non_blocking_unknown_keys: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, value: float) -> float:
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value


class ActiveDispatchRef(BaseModel):
    agent_message_id: str
    agent_id: str
    status: str


class PlannerActionRecord(BaseModel):
    action: str
    reasoning: str
    created_at: datetime = Field(default_factory=utcnow)


class PlannerAction(BaseModel):
    planner_action_schema_version: int = 2
    action: PlannerActionType
    reasoning: str
    targets: list[PlannedDelegateTarget] = Field(default_factory=list)
    questions: list[PlannerQuestion] = Field(default_factory=list)
    synthesis_instruction: str | None = None
    failure_reason: str | None = None
    completion_evidence: CompletionEvidence | None = None


class DispatchIntent(BaseModel):
    step_id: str
    step_target_id: str
    dispatch_intent_id: str
    planned_agent_message_id: str
    agent_id: str
    task: str
    task_hash: str
    status: str = "planned"
    depends_on: list[str] = Field(default_factory=list)
    parallel_group: str | None = None
    required_resource_refs: list[str] = Field(default_factory=list)
    context_refs: list[DispatchContentRef] = Field(default_factory=list)
    artifact_refs: list[DispatchContentRef] = Field(default_factory=list)
    attachment_refs: list[DispatchContentRef] = Field(default_factory=list)
    selected_resource_fingerprints: list[str] = Field(default_factory=list)
    expected_outputs: list[DispatchExpectedOutput] = Field(default_factory=list)
    repair_of_intent_id: str | None = None
    attachment_policy: Literal["explicit_refs_only", "compatible_only"] = (
        "explicit_refs_only"
    )


class AgentOutputRecord(BaseModel):
    agent_message_id: str
    agent_id: str
    status: str
    text: str | None = None
    artifact_keys: list[str] = Field(default_factory=list)
    error: str | None = None
    a2a_task_id: str | None = None
    a2a_context_id: str | None = None
    status_message: str | None = None
    interactive_state: str | None = None
    requires_auth: bool = False
    requires_policy: bool = False


class DelegationOutcomeRecord(BaseModel):
    outcome_id: str
    dispatch_intent_id: str
    agent_id: str
    goal_family_fingerprint: str
    goal_revision_fingerprint: str
    attempt_fingerprint: str
    result_fingerprint: str | None = None
    status: Literal["fulfilled", "partial", "blocked", "no_progress", "failed"]
    satisfied_output_keys: list[str] = Field(default_factory=list)
    missing_output_keys: list[str] = Field(default_factory=list)
    remaining_required_obligations: list[str] = Field(default_factory=list)
    newly_satisfied_required_obligations: list[str] = Field(default_factory=list)
    changed_artifact_keys: list[str] = Field(default_factory=list)
    changed_fact_keys: list[str] = Field(default_factory=list)
    open_failure_ids: list[str] = Field(default_factory=list)
    assumptions: list[AssumptionRecord] = Field(default_factory=list)
    unknowns: list[UnknownRecord] = Field(default_factory=list)
    blockers: list[BlockerRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class PendingAgentContinuation(BaseModel):
    continuation_id: str
    source_intent_id: str
    source_agent_message_id: str
    agent_id: str
    goal_family_fingerprint: str
    goal_revision_fingerprint: str
    a2a_task_id: str
    a2a_context_id: str
    attempted_resource_fingerprints: list[str] = Field(default_factory=list)
    status: Literal["open", "resuming", "resolved", "abandoned"] = "open"
    updated_at: datetime = Field(default_factory=utcnow)


class GoalFamilyDispositionRecord(BaseModel):
    event_id: str
    goal_family_fingerprint: str
    through_goal_revision_fingerprint: str
    status: Literal["abandoned", "superseded"]
    reason: str
    replacement_goal_family_fingerprint: str | None = None


class OrchestrationRunState(BaseModel):
    run_id: str
    room_id: str
    user_message_id: str
    goal: str
    candidate_agent_ids: list[str]
    candidate_scope: CandidateScopeSnapshot | None = None
    client_request_id: str | None = None
    status: OrchestrationStatus = OrchestrationStatus.CREATED
    schema_version: int = 2
    state_version: int = 0
    facts: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Unresolved completion blockers; remove entries when they are resolved."
        ),
    )
    agent_outputs: list[AgentOutputRecord] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    completion_criteria: list[dict[str, Any]] = Field(default_factory=list)
    dispatch_intents: list[DispatchIntent] = Field(default_factory=list)
    decision_log: list[dict[str, Any]] = Field(default_factory=list)
    pending_hitl_request_ids: list[str] = Field(default_factory=list)
    summary_intent_id: str | None = None
    summary_message_id: str | None = None
    step_budget: int = 8
    steps_used: int = 0
    participant_snapshot: ParticipantSnapshot | None = None
    system_agent_message_id: str | None = None
    active_dispatches: list[ActiveDispatchRef] = Field(default_factory=list)
    last_planner_action: PlannerActionRecord | None = None
    completion_evidence: CompletionEvidence | None = None
    terminal_reason: str | None = None
    open_failures: list[OpenFailureRecord] = Field(default_factory=list)
    delegation_outcomes: list[DelegationOutcomeRecord] = Field(default_factory=list)
    pending_agent_continuations: list[PendingAgentContinuation] = Field(
        default_factory=list
    )
    goal_family_dispositions: list[GoalFamilyDispositionRecord] = Field(
        default_factory=list
    )
    assumptions: list[AssumptionRecord] = Field(default_factory=list)
    unknowns: list[UnknownRecord] = Field(default_factory=list)
    blockers: list[BlockerRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class OrchestrationRunEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    room_id: str
    type: OrchestrationEventType
    state_version: int
    payload: dict[str, Any] = Field(default_factory=dict)
    causation_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
