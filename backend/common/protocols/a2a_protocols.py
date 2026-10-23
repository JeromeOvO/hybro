from typing import Protocol, runtime_checkable

from common.dto import (
    AgentCardSnapshot,
)


@runtime_checkable
class RoomRouteRecord(Protocol):
    pass


@runtime_checkable
class SSEUserMessageRecord(Protocol):
    pass


@runtime_checkable
class AgentCardResolver(Protocol):
    async def resolve_card(self, agent_url: str) -> AgentCardSnapshot | None:
        """Raises UnsupportedAgentProtocolError for a pre-1.0 card."""
        ...

    async def supports_push_notifications(self, agent_url: str) -> bool: ...
    async def supports_streaming(self, agent_url: str) -> bool: ...


@runtime_checkable
class RoomRouteReader(Protocol):
    async def get_room_by_room_id(self, room_id: str) -> RoomRouteRecord | None: ...


@runtime_checkable
class SSEStateReader(RoomRouteReader, Protocol):
    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> SSEUserMessageRecord | None: ...


__all__ = [
    "AgentCardResolver",
    "RoomRouteRecord",
    "RoomRouteReader",
    "SSEStateReader",
    "SSEUserMessageRecord",
]
