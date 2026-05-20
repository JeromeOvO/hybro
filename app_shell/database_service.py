from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from models.agent_group import AgentGroup
from models.room import Room, RoomAgentMessage, RoomUserMessage

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@runtime_checkable
class A2ATaskReader(Protocol):
    async def get_pending_task_messages_for_user(
        self, user_id: str, non_terminal_states: list[str]
    ) -> list[RoomAgentMessage]: ...
    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RoomAgentMessage | None: ...
    async def get_task_messages_for_room(
        self, room_id: str, *, limit: int = 50
    ) -> list[RoomAgentMessage]: ...
    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> RoomUserMessage | None: ...
    async def get_room_by_room_id(self, room_id: str) -> Room | None: ...


@runtime_checkable
class AgentGroupStore(Protocol):
    async def add_agent_group(self, agent_group: AgentGroup) -> bool: ...
    async def delete_agent_group(self, group_id: str) -> bool: ...
    async def get_agent_group_by_id(self, group_id: str) -> AgentGroup | None: ...
    async def get_agent_groups_by_owner(self, owner_id: str) -> list[AgentGroup]: ...
    async def update_agent_group(
        self, group_id: str, updates: dict[str, JsonValue]
    ) -> AgentGroup | None: ...


__all__ = ["A2ATaskReader", "AgentGroupStore"]
