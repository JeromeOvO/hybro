from datetime import datetime
from typing import Any
from uuid import uuid4

from a2a.types import Task
from pydantic import BaseModel, Field

from common.utils.time import utcnow


class Room(BaseModel):
    room_id: str = Field(default_factory=lambda: uuid4().hex)
    room_name: str
    room_owner_id: str
    room_owner_name: str
    room_agent_set: dict[str, str] = Field(
        default_factory=dict
    )  # key: agent_id, value: agent_name
    room_created_at: datetime = Field(default_factory=utcnow)
    applied_from_group: str | None = (
        None  # Group ID if agents were applied from a group
    )
    extend_info: Any | None = None
    # Track which user message is currently being processed (null = idle)
    # Used to restore "Processing your request..." placeholder on page refresh
    processing_message_id: str | None = None


class Message(BaseModel):
    room_id: str
    message_id: str
    message_created_at: datetime = Field(default_factory=utcnow)


class MessageContent(BaseModel):
    # markdown
    message_text: str | None = None
    message_task: Task | None = None


class RoomMessage(Message):
    """Unified room message format for both user and agent messages"""

    message_type: str  # "user" or "agent"
    user_id: str | None = None
    agent_id: str | None = None
    related_message_id: str | None = None
    message_content: MessageContent
    # Step tracking from task decomposition (1-indexed) - included for agent messages
    step_number: int | None = None
    total_steps: int | None = None
    # Task timestamp for staleness detection (only set for agent messages with tasks)
    task_updated_at: datetime | None = None
    # Task description being processed (only set for agent messages with tasks)
    task_content: str | None = None


class RoomUserMessage(RoomMessage):
    message_type: str = "user"
    extend_info: Any | None = None


class RoomAgentMessage(RoomMessage):
    message_type: str = "agent"
    extend_info: Any | None = None
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
