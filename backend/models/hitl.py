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

from pydantic import BaseModel, Field

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
    """Type of prompt to render in the frontend."""

    TEXT = "text"
    CHOICE = "choice"
    CONFIRMATION = "confirmation"


class HITLStatus(str, Enum):
    """Lifecycle status of a single HITL request."""

    PENDING = "pending"
    PROCESSING = "processing"
    RESPONDED = "responded"
    EXPIRED = "expired"
    CANCELED = "canceled"


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

    # Dynamic orchestration v2 linkage.  Optional for legacy HITL records.
    orchestration_run_id: str | None = None
    orchestration_schema_version: int | None = None

    # The question
    prompt: str
    prompt_type: HITLPromptType = HITLPromptType.TEXT
    choices: list[str] | None = None

    # Multi-question grouping (set when a single CLARIFY emits N questions)
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


# ---------------------------------------------------------------------------
# HITLResponseRequest — REST request body (not persisted separately)
# ---------------------------------------------------------------------------


class HITLResponseRequest(BaseModel):
    """REST request body for POST /rooms/{room_id}/hitl/respond."""

    request_id: str
    user_input: str = Field(..., min_length=1, max_length=10_000)
