from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from common.dto import (
    AgentCardSnapshot,
    AgentStreamEvent,
    AgentTaskResult,
    InternalAgentMessage,
)


@runtime_checkable
class A2ATaskStatusMessage(Protocol):
    pass


@runtime_checkable
class RoomRouteRecord(Protocol):
    pass


@runtime_checkable
class SSEUserMessageRecord(Protocol):
    pass


@runtime_checkable
class AgentTransport(Protocol):
    async def send_message(
        self,
        agent_url: str,
        message: InternalAgentMessage,
        *,
        user_id: str | None = None,
        accepted_output_modes: Sequence[str] | None = None,
    ) -> AgentTaskResult: ...

    async def stream_message(
        self,
        agent_url: str,
        message: InternalAgentMessage,
        *,
        user_id: str | None = None,
        accepted_output_modes: Sequence[str] | None = None,
    ) -> AsyncIterator[AgentStreamEvent]: ...


@runtime_checkable
class AgentCardResolver(Protocol):
    async def resolve_card(self, agent_url: str) -> AgentCardSnapshot | None: ...
    async def supports_push_notifications(self, agent_url: str) -> bool: ...
    async def supports_streaming(self, agent_url: str) -> bool: ...


@runtime_checkable
class A2ATaskStatusReader(Protocol):
    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> A2ATaskStatusMessage | None: ...
    async def get_task_messages_for_room(
        self, room_id: str, *, limit: int = 50
    ) -> list[A2ATaskStatusMessage]: ...
    async def get_pending_task_messages_for_user(
        self, user_id: str, states: list[str]
    ) -> list[A2ATaskStatusMessage]: ...


@runtime_checkable
class RoomRouteReader(Protocol):
    async def get_room_by_room_id(self, room_id: str) -> RoomRouteRecord | None: ...


@runtime_checkable
class SSEStateReader(RoomRouteReader, Protocol):
    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> SSEUserMessageRecord | None: ...


__all__ = [
    "A2ATaskStatusReader",
    "A2ATaskStatusMessage",
    "AgentCardResolver",
    "AgentTransport",
    "RoomRouteRecord",
    "RoomRouteReader",
    "SSEStateReader",
    "SSEUserMessageRecord",
]
