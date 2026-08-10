from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from common.dto import (
    AgentCardSnapshot,
    AgentInfo,
    AgentMatchResult,
    HubAgentCounts,
    HubAgentDescriptor,
    LocalAgentUpsertResult,
    SyncedHubAgent,
)


@runtime_checkable
class AgentRegistry(Protocol):
    async def get_agent(self, agent_id: str) -> AgentInfo | None: ...
    async def get_agent_card(self, agent_id: str) -> AgentCardSnapshot | None: ...
    async def get_agents_by_ids(self, agent_ids: list[str]) -> list[AgentInfo]: ...
    async def get_agent_by_url(self, url: str) -> AgentInfo | None: ...
    async def is_agent_healthy(self, agent_id: str) -> bool: ...
    async def is_directly_callable(self, agent_id: str) -> bool: ...


@runtime_checkable
class AgentMatcher(Protocol):
    async def match_agents(
        self,
        query: str,
        limit: int = 5,
        filter_ids: list[str] | None = None,
        respect_visibility: bool = True,
        requesting_user_id: str | None = None,
    ) -> list[AgentMatchResult]: ...


@runtime_checkable
class AgentMessageMatcher(Protocol):
    async def match_for_message(
        self,
        query: str,
        *,
        limit: int = 5,
        filter_ids: list[str] | None = None,
        requesting_user_id: str | None = None,
        required_input_modes: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class AgentExclusionReader(Protocol):
    async def get_excluded_agent_ids(self) -> frozenset[str]: ...


@runtime_checkable
class AgentManagement(Protocol):
    async def register_agent(
        self, url: str, provider_id: str, **kwargs
    ) -> AgentInfo: ...
    async def delete_agent(self, agent_id: str, provider_id: str) -> bool: ...
    async def update_agent(self, agent_id: str, updates: dict) -> AgentInfo | None: ...
    async def list_agents(self, provider_id: str) -> list[AgentInfo]: ...
    async def list_public_agents(self, limit: int = 50) -> list[AgentInfo]: ...


@runtime_checkable
@runtime_checkable
class AgentRegistryWriter(Protocol):
    async def sync_hub_agents(
        self,
        hub_id: str,
        owner_user_id: str,
        agents: list[HubAgentDescriptor],
        prune_missing: bool = True,
    ) -> list[SyncedHubAgent]: ...

    async def mark_hub_agents_offline(self, hub_id: str) -> None: ...

    async def upsert_local_agent(
        self,
        discovery_url: str,
        card: AgentCardSnapshot,
    ) -> LocalAgentUpsertResult: ...

    async def list_local_agent_ids(self) -> list[str]: ...

    async def mark_local_agents_inactive(self, agent_ids: list[str]) -> int: ...


@runtime_checkable
class HubAgentStatusReader(Protocol):
    async def count_hub_agents(self, hub_id: str) -> HubAgentCounts: ...


@runtime_checkable
class AgentCallCounter(Protocol):
    async def increment_agent_call_count(
        self, agent_id: str, *, success: bool
    ) -> None: ...


__all__ = [
    "AgentCallCounter",
    "AgentManagement",
    "AgentExclusionReader",
    "AgentMatcher",
    "AgentMessageMatcher",
    "AgentRegistry",
    "AgentRegistryWriter",
    "HubAgentStatusReader",
]
