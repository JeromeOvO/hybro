from typing import AsyncIterator, Protocol, runtime_checkable

from common.dto import (
    AgentCardSnapshot,
    AgentStreamEvent,
    AgentTaskResult,
    InternalAgentMessage,
)


@runtime_checkable
class AgentTransport(Protocol):
    async def send_message(
        self, agent_url: str, message: InternalAgentMessage, **kwargs
    ) -> AgentTaskResult: ...

    async def stream_message(
        self, agent_url: str, message: InternalAgentMessage, **kwargs
    ) -> AsyncIterator[AgentStreamEvent]: ...


@runtime_checkable
class AgentCardResolver(Protocol):
    async def resolve_card(self, agent_url: str) -> AgentCardSnapshot | None: ...
    async def supports_push_notifications(self, agent_url: str) -> bool: ...
    async def supports_streaming(self, agent_url: str) -> bool: ...


__all__ = ["AgentCardResolver", "AgentTransport"]
