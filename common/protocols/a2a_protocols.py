from typing import AsyncIterator, Protocol, runtime_checkable

from common.dto import AgentCardSnapshot, AgentStreamEvent, AgentTaskResult, InternalAgentMessage


@runtime_checkable
class AgentTransport(Protocol):
    async def send_message(self, message: InternalAgentMessage) -> AgentTaskResult: ...
    async def send_stream(
        self, message: InternalAgentMessage
    ) -> AsyncIterator[AgentStreamEvent]: ...
    async def cancel_task(self, agent_id: str, task_id: str) -> AgentTaskResult: ...


@runtime_checkable
class AgentCardResolver(Protocol):
    async def resolve(self, agent_id: str, url: str) -> AgentCardSnapshot: ...


__all__ = [
    "AgentCardResolver",
    "AgentTransport",
]
