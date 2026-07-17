from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

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
    kind: str
    required: bool = True
    description: str | None = None


class PlannedDelegateTarget(BaseModel):
    agent_id: str
    task: str
    agent_name: str | None = None
    context_refs: list[DispatchContentRef] = Field(default_factory=list)
    artifact_refs: list[DispatchContentRef] = Field(default_factory=list)
    attachment_refs: list[DispatchContentRef] = Field(default_factory=list)
    expected_outputs: list[DispatchExpectedOutput] = Field(default_factory=list)
    attachment_policy: Literal["explicit_refs_only", "compatible_only"] = (
        "explicit_refs_only"
    )


class PlannerQuestion(BaseModel):
    prompt: str
    prompt_type: Literal["text", "choice", "confirmation"] = "text"
    choices: list[str] | None = None


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


class CompletionEvidence(BaseModel):
    satisfied_criteria: list[str] = Field(default_factory=list)
    referenced_fact_ids: list[str] = Field(default_factory=list)
    referenced_artifact_keys: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    final_answer_intent: str
    confidence: float

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
    context_refs: list[DispatchContentRef] = Field(default_factory=list)
    artifact_refs: list[DispatchContentRef] = Field(default_factory=list)
    attachment_refs: list[DispatchContentRef] = Field(default_factory=list)
    expected_outputs: list[DispatchExpectedOutput] = Field(default_factory=list)
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
