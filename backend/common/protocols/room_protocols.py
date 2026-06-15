from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from common.dto import (
    AgentInfo,
    AgentMessageInput,
    CreateRoomRequest,
    HubPublishLineageSnapshot,
    MembershipUpdateRequest,
    RoomInfo,
    RoomMessageInfo,
    SavedAgentGroupSnapshot,
    SavedUserMessage,
    UserMessageInput,
)


@runtime_checkable
class A2ATaskReader(Protocol):
    async def get_pending_task_messages_for_user(
        self, user_id: str, states: list[str]
    ) -> list[dict[str, Any]]:
        ...
    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> dict[str, Any] | None:
        ...
    async def get_room_by_room_id(self, room_id: str) -> RoomInfo | None:
        ...
    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> dict[str, Any] | None:
        ...
    async def get_task_messages_for_room(
        self, room_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class RoomRegistry(Protocol):
    async def get_room(self, room_id: str) -> RoomInfo | None: ...
    async def get_room_agents(self, room_id: str) -> list[str]: ...
    async def get_room_owner(self, room_id: str) -> str | None: ...


@runtime_checkable
class RoomManagement(Protocol):
    async def create_room(self, request: CreateRoomRequest) -> RoomInfo: ...
    async def delete_room(self, room_id: str, owner_id: str) -> bool: ...
    async def update_room(self, room_id: str, updates: dict) -> RoomInfo | None: ...
    async def update_membership(
        self, room_id: str, request: MembershipUpdateRequest
    ) -> RoomInfo: ...


@runtime_checkable
class RoomMessageStore(Protocol):
    async def save_user_message(
        self, room_id: str, message: UserMessageInput
    ) -> SavedUserMessage: ...
    async def save_agent_message(self, room_id: str, message: AgentMessageInput) -> str: ...
    async def update_agent_message_status(
        self, message_id: str, status: str, **kwargs
    ) -> bool: ...
    async def get_message(self, message_id: str) -> RoomMessageInfo | None: ...


@runtime_checkable
class RoomHistoryReader(Protocol):
    async def get_messages_for_room(
        self, room_id: str, limit: int = 100, before: datetime | None = None
    ) -> list[RoomMessageInfo]: ...

    async def get_messages_by_ids(
        self, message_ids: list[str]
    ) -> list[RoomMessageInfo]: ...
    async def get_message_thread(
        self, parent_message_id: str
    ) -> list[RoomMessageInfo]: ...


@runtime_checkable
class RoomOwnershipReader(Protocol):
    async def get_room_owner(self, room_id: str) -> str | None: ...
    async def verify_room_agent_membership(self, room_id: str, agent_id: str) -> bool: ...
    async def verify_room_hub_ownership(self, room_id: str, hub_id: str) -> bool: ...


@runtime_checkable
class HubPublishAuthorizationReader(Protocol):
    async def authorize_hub_publish(
        self, *, hub_id: str, owner_id: str, room_id: str, agent_message_id: str
    ) -> HubPublishLineageSnapshot | None: ...


@runtime_checkable
class HubPublishLineageReader(Protocol):
    async def get_hub_publish_lineage(
        self, *, room_id: str, agent_message_id: str
    ) -> HubPublishLineageSnapshot | None: ...


@runtime_checkable
class MessageCancellationReader(Protocol):
    async def is_message_cancelled(self, message_id: str) -> bool: ...


@runtime_checkable
class RoomAgentTaskTracker(Protocol):
    async def track_hub_task(self, message_id: str, task_data: dict) -> None: ...


@runtime_checkable
class RoomMembershipSeedSource(Protocol):
    async def get_saved_group(self, group_id: str) -> SavedAgentGroupSnapshot | None: ...
    async def list_current_agents(self, user_id: str | None) -> list[AgentInfo]: ...


__all__ = [
    "A2ATaskReader",
    "HubPublishAuthorizationReader",
    "HubPublishLineageReader",
    "MessageCancellationReader",
    "RoomAgentTaskTracker",
    "RoomHistoryReader",
    "RoomManagement",
    "RoomMembershipSeedSource",
    "RoomMessageStore",
    "RoomOwnershipReader",
    "RoomRegistry",
]
