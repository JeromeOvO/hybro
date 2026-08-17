"""Human-in-the-Loop (HITL) data models.

Defines the models for HITL request/response lifecycle, event types,
and the InterruptKind enum used as the routing key in continuation payloads.

See docs/HITL_DESIGN.md for full design details.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from common.utils.time import utcnow

# ---------------------------------------------------------------------------
# InterruptKind — routing key for _resume_supervisor()
# ---------------------------------------------------------------------------


class InterruptKind(str, Enum):
    """The interrupt_kind field in every continuation payload is the single
    routing signal for _resume_supervisor().

    Backward compatibility: if the field is absent (legacy push-notification
    continuations saved before this design), assume PUSH_NOTIFICATION.
    """

    PUSH_NOTIFICATION = "push_notification"
    HITL_AGENT = "hitl_agent"
    HITL_SUPERVISOR = "hitl_supervisor"


# ---------------------------------------------------------------------------
# HITL lifecycle enums
# ---------------------------------------------------------------------------


class HITLEventType(str, Enum):
    """Events in the human-in-the-loop lifecycle."""

    INPUT_REQUESTED = "hitl_request"
    INPUT_RECEIVED = "hitl_input_received"
    INPUT_EXPIRED = "hitl_input_expired"
    INPUT_CANCELED = "hitl_input_canceled"
    ERROR = "hitl_error"


class HITLPromptType(str, Enum):
    """Type of control the client should render for a HITL question."""

    TEXT = "text"
    TEXTAREA = "textarea"
    CHOICE = "choice"
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    CONFIRMATION = "confirmation"
    APPROVAL = "approval"
    AUTHENTICATION = "authentication"
    DATE = "date"
    FILE = "file"


class HITLStatus(str, Enum):
    """Lifecycle status of a single HITL request."""

    PENDING = "pending"
    PROCESSING = "processing"  # Legacy request-level delivery lease.
    ANSWER_RECORDED = "answer_recorded"
    RESPONDED = "responded"
    EXPIRED = "expired"
    CANCELED = "canceled"


class HITLInteractionStatus(str, Enum):
    """Durable lifecycle of one user-visible interaction/questionnaire."""

    MATERIALIZING = "materializing"
    OPEN = "open"
    PARTIALLY_ANSWERED = "partially_answered"
    ANSWERS_RECORDED = "answers_recorded"
    APPLYING = "applying"
    DELIVERY_UNCERTAIN = "delivery_uncertain"
    APPLIED = "applied"
    CANCELED = "canceled"
    EXPIRED = "expired"
    FAILED = "failed"


class HITLResumeCommandStatus(str, Enum):
    """State of the durable remote A2A continuation command."""

    PENDING = "pending"
    DELIVERING = "delivering"
    ACKNOWLEDGED = "acknowledged"
    PROJECTED = "projected"
    RETRYABLE_ERROR = "retryable_error"
    DELIVERY_UNCERTAIN = "delivery_uncertain"
    PERMANENT_FAILURE = "permanent_failure"


# ---------------------------------------------------------------------------
# HITLRequest — persisted to MongoDB
# ---------------------------------------------------------------------------


class HITLRequest(BaseModel):
    """A request for human input, emitted as an event and persisted to DB."""

    request_id: str = Field(default_factory=lambda: uuid4().hex)
    room_id: str
    user_message_id: str

    # What triggered this
    source: Literal["agent", "supervisor"]
    source_step_id: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None

    # A2A continuation context (for agent-sourced requests)
    a2a_task_id: str | None = None
    a2a_context_id: str | None = None
    continuation_message_id: str | None = None

    # Frontend display: when set, the SSE event uses this as `message_id`
    # instead of continuation_message_id.  This prevents supervisor CLARIFY
    # HITL prompts from overwriting the user's own chat message entity.
    display_message_id: str | None = None
    client_request_id: str | None = None

    # Durable orchestration linkage.
    orchestration_run_id: str | None = None

    # The question
    prompt: str
    # Server-side fingerprint of the unsanitized agent prompt. It is persisted
    # for no-progress detection but is never included in public HITL DTOs.
    agent_prompt_hash: str | None = None
    prompt_type: HITLPromptType = HITLPromptType.TEXT
    choices: list[str] | None = None

    # Multi-question grouping (set when a single CLARIFY emits N questions)
    # interaction_id is the aggregate identity. Legacy records synthesize it
    # deterministically from group_id/request_id during parsing.
    interaction_id: str | None = None
    interaction_status: HITLInteractionStatus | None = None
    application_status: str | None = None
    application_error: str | None = None
    group_id: str | None = None
    group_total: int | None = None
    group_index: int | None = None

    # Lifecycle
    status: HITLStatus = HITLStatus.PENDING
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)

    # Response (populated when status == "responded")
    user_input: str | None = None
    responded_at: datetime | None = None
    responded_by_user_id: str | None = None

    # Durable owning-run terminal reconciliation for cancel/expiry failures.
    owning_run_terminal_status: Literal["canceled", "failed"] | None = None
    owning_run_terminal_reason: str | None = None

    @model_validator(mode="after")
    def synthesize_interaction_id(self) -> HITLRequest:
        if not self.interaction_id:
            self.interaction_id = self.group_id or self.request_id
        return self


class HITLInteraction(BaseModel):
    """Durable aggregate that owns answer collection and application."""

    schema_version: int = 2
    interaction_id: str
    room_id: str
    user_message_id: str
    orchestration_run_id: str | None = None
    source: Literal["agent", "supervisor"]
    request_ids: list[str] = Field(default_factory=list)
    expected_request_count: int = Field(ge=1, le=100)
    required_request_ids: list[str] = Field(default_factory=list)
    status: HITLInteractionStatus = HITLInteractionStatus.MATERIALIZING
    version: int = Field(default=1, ge=1)
    expires_at: datetime | None = None
    answer_request_ids: list[str] = Field(default_factory=list)
    answer_digest: str | None = None
    application_revision: int = Field(default=0, ge=0)
    application_claim_id: str | None = None
    application_lease_expires_at: datetime | None = None
    application_attempts: int = Field(default=0, ge=0)
    application_error: str | None = None
    application_started_at: datetime | None = None
    run_projection_status: Literal["pending", "applying", "applied", "failed"] = (
        "pending"
    )
    run_projection_claim_id: str | None = None
    run_projection_lease_expires_at: datetime | None = None
    run_projection_error: str | None = None
    applied_at: datetime | None = None
    terminal_reason: str | None = None
    terminal_reconciled: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def validate_state(self) -> HITLInteraction:
        if len(set(self.request_ids)) != len(self.request_ids):
            raise ValueError("request_ids must be unique")
        if len(self.request_ids) > self.expected_request_count:
            raise ValueError("request count exceeds expected_request_count")
        if not set(self.required_request_ids).issubset(self.request_ids):
            raise ValueError("required_request_ids must be attached")
        if not set(self.answer_request_ids).issubset(self.request_ids):
            raise ValueError("answer_request_ids must be attached")
        complete_states = {
            HITLInteractionStatus.ANSWERS_RECORDED,
            HITLInteractionStatus.APPLYING,
            HITLInteractionStatus.DELIVERY_UNCERTAIN,
            HITLInteractionStatus.APPLIED,
        }
        if self.status in complete_states and not set(
            self.required_request_ids
        ).issubset(self.answer_request_ids):
            raise ValueError("complete interaction is missing required answers")
        if self.status == HITLInteractionStatus.APPLIED and self.applied_at is None:
            raise ValueError("applied interaction requires applied_at")
        if (
            self.status == HITLInteractionStatus.APPLYING
            and not self.application_claim_id
        ):
            raise ValueError("applying interaction requires an application claim")
        return self


class HITLResumeCommand(BaseModel):
    """Durable journal entry for one remote continuation application."""

    schema_version: int = 2
    command_id: str
    kind: Literal["a2a_resume"] = "a2a_resume"
    interaction_id: str
    application_revision: int = Field(ge=1)
    task_id: str
    context_id: str
    continuation_message_id: str
    display_message_id: str | None = None
    outbound_message_id: str
    answer_request_ids: list[str] = Field(min_length=1)
    answer_digest: str
    status: HITLResumeCommandStatus = HITLResumeCommandStatus.PENDING
    version: int = Field(default=1, ge=1)
    claim_id: str | None = None
    lease_expires_at: datetime | None = None
    attempts: int = Field(default=0, ge=0)
    next_attempt_at: datetime | None = None
    response_snapshot: dict | None = None
    error_code: str | None = None
    error_message: str | None = None
    uncertain_since: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def validate_continuation(self) -> HITLResumeCommand:
        for field_name in (
            "task_id",
            "context_id",
            "continuation_message_id",
            "outbound_message_id",
        ):
            value = getattr(self, field_name)
            if not value.strip():
                raise ValueError(f"{field_name} must not be blank")
            if field_name in {"task_id", "context_id"} and value.startswith(
                ("pending-", "relay-pending-")
            ):
                raise ValueError(f"{field_name} must be authoritative")
        if self.status == HITLResumeCommandStatus.DELIVERING and not self.claim_id:
            raise ValueError("delivering command requires a claim")
        if (
            self.status == HITLResumeCommandStatus.DELIVERY_UNCERTAIN
            and self.uncertain_since is None
        ):
            raise ValueError("delivery-uncertain command requires uncertain_since")
        return self


class HITLSupervisorEffectCommand(BaseModel):
    """Durable journal for the local supervisor continuation effect."""

    schema_version: int = 2
    command_id: str
    kind: Literal["supervisor_resume"] = "supervisor_resume"
    interaction_id: str
    application_revision: int = Field(ge=1)
    orchestration_run_id: str
    answer_request_ids: list[str] = Field(min_length=1)
    answer_digest: str
    status: HITLResumeCommandStatus = HITLResumeCommandStatus.PENDING
    version: int = Field(default=1, ge=1)
    claim_id: str | None = None
    lease_expires_at: datetime | None = None
    attempts: int = Field(default=0, ge=0)
    next_attempt_at: datetime | None = None
    response_snapshot: dict | None = None
    error_code: str | None = None
    error_message: str | None = None
    uncertain_since: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def validate_effect(self) -> HITLSupervisorEffectCommand:
        if not self.orchestration_run_id.strip():
            raise ValueError("orchestration_run_id must not be blank")
        if self.status == HITLResumeCommandStatus.DELIVERING and not self.claim_id:
            raise ValueError("delivering command requires a claim")
        return self


# ---------------------------------------------------------------------------
# HITLResponseRequest — REST request body (not persisted separately)
# ---------------------------------------------------------------------------


class HITLResponseRequest(BaseModel):
    """REST request body for POST /rooms/{room_id}/hitl/respond."""

    request_id: str
    user_input: str = Field(..., min_length=1, max_length=10_000)


class HITLBatchAnswer(BaseModel):
    """One answer in an atomic questionnaire submission."""

    request_id: str = Field(..., min_length=1, max_length=128)
    user_input: str = Field(..., min_length=1, max_length=10_000)


class HITLBatchResponseRequest(BaseModel):
    """Submit every required answer for one durable HITL interaction."""

    interaction_id: str = Field(..., min_length=1, max_length=128)
    answers: list[HITLBatchAnswer] = Field(..., min_length=1, max_length=100)
    client_request_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_unique_answers(self) -> HITLBatchResponseRequest:
        request_ids = [answer.request_id for answer in self.answers]
        if len(set(request_ids)) != len(request_ids):
            raise ValueError("answers must contain unique request_ids")
        return self
