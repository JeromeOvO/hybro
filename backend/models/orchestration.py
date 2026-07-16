from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

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


class PlannedDelegateTarget(BaseModel):
    agent_id: str
    task: str
    agent_name: str | None = None


class PlannerQuestion(BaseModel):
    prompt: str
    prompt_type: Literal["text", "choice", "confirmation"] = "text"
    choices: list[str] | None = None


class PlannerAction(BaseModel):
    planner_action_schema_version: int = 2
    action: PlannerActionType
    reasoning: str
    targets: list[PlannedDelegateTarget] = Field(default_factory=list)
    questions: list[PlannerQuestion] = Field(default_factory=list)
    synthesis_instruction: str | None = None
    failure_reason: str | None = None


class DispatchIntent(BaseModel):
    step_id: str
    step_target_id: str
    dispatch_intent_id: str
    planned_agent_message_id: str
    agent_id: str
    task: str
    task_hash: str
    status: str = "planned"


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
    client_request_id: str | None = None
    status: OrchestrationStatus = OrchestrationStatus.CREATED
    schema_version: int = 2
    state_version: int = 0
    facts: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[dict[str, Any]] = Field(default_factory=list)
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
