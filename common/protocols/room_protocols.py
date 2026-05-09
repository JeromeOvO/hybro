from datetime import datetime
from typing import Protocol, runtime_checkable

from common.dto import (
    AgentMessageInput,
    CreateRoomRequest,
    MembershipUpdateRequest,
    RoomInfo,
    RoomMessageInfo,
    SavedUserMessage,
    UserMessageInput,
)


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
    async def verify_room_agent_membership(self, room_id: str, agent_id: str) -> bool: ...
    async def verify_room_hub_ownership(self, room_id: str, hub_id: str) -> bool: ...


__all__ = [
    "RoomHistoryReader",
    "RoomManagement",
    "RoomMessageStore",
    "RoomOwnershipReader",
    "RoomRegistry",
]
