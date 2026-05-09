from typing import Protocol, runtime_checkable

from common.dto import AgentInfo, AgentMatchResult, HubAgentDescriptor, SyncedHubAgent


@runtime_checkable
class AgentRegistry(Protocol):
    async def get_agent(self, agent_id: str) -> AgentInfo | None: ...
    async def list_agents(self, active_only: bool = False) -> list[AgentInfo]: ...
    async def list_agents_by_provider(self, provider_id: str) -> list[AgentInfo]: ...


@runtime_checkable
class AgentMatcher(Protocol):
    async def match(
        self,
        query: str,
        candidates: list[AgentInfo] | None = None,
        limit: int | None = None,
    ) -> list[AgentMatchResult]: ...


@runtime_checkable
class AgentManagement(Protocol):
    async def register_agent(self, agent: AgentInfo) -> AgentInfo: ...
    async def update_agent(self, agent_id: str, agent: AgentInfo) -> AgentInfo: ...
    async def remove_agent(self, agent_id: str) -> bool: ...


@runtime_checkable
class AgentRegistryWriter(Protocol):
    async def sync_agents(
        self, hub_id: str, agents: list[HubAgentDescriptor]
    ) -> list[SyncedHubAgent]: ...
    async def mark_hub_agents_offline(self, hub_id: str) -> int: ...


__all__ = [
    "AgentManagement",
    "AgentMatcher",
    "AgentRegistry",
    "AgentRegistryWriter",
]
