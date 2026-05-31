from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from common.types import Task
from common.utils.time import utcnow
from models.quote import UserQuoteCreatePayload


class CoordinatorAgentId(StrEnum):
    """Well-known synthetic agent IDs used for coordinator/system-generated messages.

    These are never real agent IDs in the database; they identify the source of
    messages produced by the orchestration layer (supervisor, debate summary, etc.).
    """

    SUPERVISOR_ERROR = "supervisor_error"
    SUPERVISOR_SYNTHESIS = "supervisor_synthesis"
    SUPERVISOR_CLARIFY = "supervisor_clarify"
    SUMMARY = "summary"
    SYSTEM = "system"
    # Deprecated — kept for historical data backward compatibility.
    # New writes must use SUMMARY instead.
    DEBATE_SUMMARY = "debate_summary"
    NON_DEBATE_SUMMARY = "non_debate_summary"


class MembershipOrigin(StrEnum):
    MANUAL = "manual"
    SAVED_GROUP = "saved_group"
    ALL_CURRENT_AGENTS = "all_current_agents"


class MembershipOriginStatus(StrEnum):
    SEEDED_NEVER_EDITED = "seeded_never_edited"
    SEEDED_EDITED = "seeded_edited"
    MANUAL = "manual"


class Room(BaseModel):
    room_id: str = Field(default_factory=lambda: uuid4().hex)
    room_name: str
    room_owner_id: str
    room_owner_name: str
    room_agent_set: dict[str, str] = Field(
        default_factory=dict
    )  # key: agent_id, value: agent_name
    room_created_at: datetime = Field(default_factory=utcnow)

    # Legacy provenance field — kept for backward compatibility during rollout.
    # Canonical fields below take precedence when present.
    applied_from_group: str | None = None

    # Canonical provenance fields
    membership_origin: MembershipOrigin | None = None
    membership_origin_status: MembershipOriginStatus | None = None
    source_group_id: str | None = None
    source_group_name: str | None = None

    extend_info: Any | None = None
    processing_message_id: str | None = None

    @field_validator("extend_info", mode="before")
    @classmethod
    def _ensure_mutable_dict(cls, v):
        """Defensive: convert FrozenDict to a plain dict so downstream code can mutate."""
        if v is None:
            return v
        if type(v) is not dict and isinstance(v, dict):
            return dict(v)
        return v


class Message(BaseModel):
    room_id: str
    message_id: str
    message_created_at: datetime = Field(default_factory=utcnow)


class UserAttachment(BaseModel):
    """A file attached to a user message. Stored alongside message in MongoDB.

    file_url is ephemeral -- generated from s3_key at read time via presigned
    URL, never persisted.
    """

    file_id: str
    s3_key: str
    mime_type: str
    file_name: str
    size_bytes: int
    file_url: str | None = Field(default=None, json_schema_extra={"readOnly": True})


class MessageContent(BaseModel):
    message_text: str | None = None
    message_task: Task | None = None
    attachments: list[UserAttachment] | None = None
    content_summary: dict | None = None

    @field_validator("message_task", mode="before")
    @classmethod
    def _coerce_task(cls, v):
        if v is None:
            return None
        if isinstance(v, Task):
            return v
        if isinstance(v, dict):
            return Task.model_validate(v)
        if hasattr(v, "model_dump"):
            return Task.model_validate(v.model_dump(mode="json"))
        return v


MAX_MESSAGE_LENGTH = 10_000


class RoomMessage(Message):
    """Unified room message format for both user and agent messages"""

    message_type: str  # "user" or "agent"
    user_id: str | None = None
    agent_id: str | None = None
    # Conversation graph / execution link (optional; see EVENT_SOURCED_TURN_LIFECYCLE_REFACTOR_DESIGN.md)
    parent_message_id: str | None = None
    run_id: str | None = None
    # Canonical frontend correlation key for turn identity.
    # For legacy rows this is backfilled to message_id via migration.
    client_request_id: str | None = None
    related_message_id: str | None = None
    message_content: MessageContent
    # Step tracking from task decomposition (1-indexed) - included for agent messages
    step_number: int | None = None
    total_steps: int | None = None
    # Task timestamp for staleness detection (only set for agent messages with tasks)
    task_updated_at: datetime | None = None
    # Task description being processed (only set for agent messages with tasks)
    task_content: str | None = None
    # Arbitrary metadata (quoted text, dispatch strategy, etc.)
    extend_info: Any | None = None


class RoomUserMessage(RoomMessage):
    message_type: str = "user"
    extend_info: Any | None = None
    processing_claimed_at: datetime | None = None
    quote_id: str | None = None
    # Ephemeral: present only on inbound API; stripped before Mongo insert (see mongodb.add_room_user_message).
    quote: UserQuoteCreatePayload | None = None

    @field_validator("extend_info", mode="before")
    @classmethod
    def _ensure_mutable_dict(cls, v):
        """Defensive: convert FrozenDict to a plain dict so downstream code can mutate."""
        if v is None:
            return v
        if type(v) is not dict and isinstance(v, dict):
            return dict(v)
        return v


class RoomAgentMessage(RoomMessage):
    message_type: str = "agent"
    extend_info: Any | None = None

    @field_validator("extend_info", mode="before")
    @classmethod
    def _ensure_mutable_dict(cls, v):
        """Defensive: convert FrozenDict to a plain dict so downstream code can mutate."""
        if v is None:
            return v
        if type(v) is not dict and isinstance(v, dict):
            return dict(v)
        return v

    # Task tracking fields (consolidated from a2a_tasks collection)
    # Note: message_id is used as the primary key for task lookups (webhook URL, etc.)
    # The following fields are set when task tracking is enabled for this message:
    webhook_token_hash: str | None = None  # Hashed token for webhook auth
    pending_continuation: dict | None = (
        None  # Queue state for resuming after push notification
    )
    last_notified_state: str | None = (
        None  # Last SSE-notified state (prevents duplicates)
    )
    agent_url: str | None = None  # Agent URL for fallback polling
    task_created_at: datetime | None = None  # Task creation timestamp
    task_updated_at: datetime | None = None  # Task last update timestamp
    task_content: str | None = None  # Task description being processed
    # Flag to indicate this message has task tracking enabled
    has_task_tracking: bool = False
    turn_id: str | None = None  # Root user message_id that triggered this processing chain
