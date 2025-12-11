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


class RoomUserMessage(RoomMessage):
    message_type: str = "user"
    extend_info: Any | None = None


class RoomAgentMessage(RoomMessage):
    message_type: str = "agent"
    extend_info: Any | None = None
