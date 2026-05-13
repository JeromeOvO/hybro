from datetime import datetime
from typing import Any, Literal

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


class MembershipSeed(FrozenDTO):
    mode: Literal["manual", "saved_group", "all_current_agents"]
    agent_ids: list[str] | None = None
    group_id: str | None = None
    requesting_user_id: str | None = None


class SavedAgentGroupSnapshot(FrozenDTO):
    group_id: str
    name: str
    owner_id: str | None = None
    type: str | None = None
    agent_ids: list[str] = Field(default_factory=list)


class MembershipUpdateRequest(FrozenDTO):
    add_agent_ids: list[str] | None = None
    remove_agent_ids: list[str] | None = None


class RoomInfo(FrozenDTO):
    room_id: str
    room_name: str
    owner_id: str
    owner_name: str | None = None
    agent_ids: list[str] = Field(default_factory=list)
    agent_set: dict[str, str] = Field(default_factory=dict)
    membership_origin: str = "manual"
    membership_origin_status: str = "active"
    source_group_id: str | None = None
    source_group_name: str | None = None
    created_at: datetime | None = None
    processing_message_id: str | None = None
    extend_info: dict[str, Any] | None = None


class CreateRoomRequest(FrozenDTO):
    owner_id: str
    owner_name: str
    room_name: str
    membership_seed: MembershipSeed


RoomCreationParams = CreateRoomRequest


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
    dispatch_root_message_id: str | None = None
    user_id: str
    user_name: str
    message: dict[str, Any] = Field(default_factory=dict)
    scope_resolution_error: dict[str, Any] | None = None


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
    "SavedAgentGroupSnapshot",
    "SavedUserMessage",
    "UserMessageInput",
]
