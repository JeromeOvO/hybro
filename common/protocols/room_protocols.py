from typing import Protocol, runtime_checkable

from common.dto import (
    AgentMessageInput,
    MembershipUpdateRequest,
    MessageRecord,
    RoomCreationParams,
    RoomInfo,
    RoomMembership,
    RoomSummary,
    SavedUserMessage,
    UserMessageInput,
)


@runtime_checkable
class RoomRegistry(Protocol):
    async def get_room(self, room_id: str) -> RoomInfo | None: ...
    async def list_rooms_by_owner(self, owner_id: str) -> list[RoomSummary]: ...


@runtime_checkable
class RoomManagement(Protocol):
    async def create_room(self, params: RoomCreationParams) -> RoomInfo: ...
    async def update_room_name(self, room_id: str, room_name: str) -> RoomInfo: ...
    async def update_membership(
        self, request: MembershipUpdateRequest
    ) -> RoomMembership: ...
    async def delete_room(self, room_id: str) -> bool: ...


@runtime_checkable
class RoomMessageStore(Protocol):
    async def create_user_message(self, message: UserMessageInput) -> SavedUserMessage: ...
    async def save_agent_message(self, message: AgentMessageInput) -> MessageRecord: ...
    async def update_agent_message(
        self, message_id: str, content: dict
    ) -> MessageRecord: ...


@runtime_checkable
class RoomHistoryReader(Protocol):
    async def list_user_messages(self, room_id: str) -> list[MessageRecord]: ...
    async def list_agent_messages(self, room_id: str) -> list[MessageRecord]: ...
    async def list_room_messages(self, room_id: str) -> list[MessageRecord]: ...


@runtime_checkable
class RoomOwnershipReader(Protocol):
    async def get_room_owner_id(self, room_id: str) -> str | None: ...
    async def user_owns_room(self, room_id: str, user_id: str) -> bool: ...


__all__ = [
    "RoomHistoryReader",
    "RoomManagement",
    "RoomMessageStore",
    "RoomOwnershipReader",
    "RoomRegistry",
]
