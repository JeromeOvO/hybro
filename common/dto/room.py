from datetime import datetime
from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class RoomSummary(FrozenDTO):
    room_id: str
    room_name: str
    owner_id: str
    owner_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    agent_ids: list[str] = Field(default_factory=list)


class RoomMembership(FrozenDTO):
    room_id: str
    agent_ids: list[str] = Field(default_factory=list)


class MessageRecord(FrozenDTO):
    room_id: str
    message_id: str
    message_type: str
    content: dict[str, Any]
    created_at: datetime | None = None
    sender_id: str | None = None
    sender_name: str | None = None
    agent_id: str | None = None
    parent_message_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoomCreationParams(FrozenDTO):
    owner_id: str
    owner_name: str
    room_name: str
    agent_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MembershipSeed(FrozenDTO):
    room_id: str
    agent_ids: list[str] = Field(default_factory=list)


class MembershipUpdateRequest(FrozenDTO):
    room_id: str
    agent_ids: list[str] = Field(default_factory=list)


class UserMessageInput(FrozenDTO):
    room_id: str
    message_text: str
    sender_id: str
    sender_name: str | None = None
    client_request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentMessageInput(FrozenDTO):
    room_id: str
    agent_id: str
    content: dict[str, Any] = Field(default_factory=dict)
    parent_message_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SavedUserMessage(FrozenDTO):
    room_id: str
    message_id: str
    sender_id: str
    content: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


RoomInfo = RoomSummary
CreateRoomRequest = RoomCreationParams
RoomMessageInfo = MessageRecord


__all__ = [
    "AgentMessageInput",
    "CreateRoomRequest",
    "MembershipSeed",
    "MembershipUpdateRequest",
    "MessageRecord",
    "RoomCreationParams",
    "RoomInfo",
    "RoomMembership",
    "RoomMessageInfo",
    "RoomSummary",
    "SavedUserMessage",
    "UserMessageInput",
]
