from __future__ import annotations

from typing import Protocol

from common.dto import AgentCardSnapshot, LocalAgentUpsertResult
from local_agents.models import DiscoveryTrigger, LocalAgentDiscoveryResult


class LocalAgentWriter(Protocol):
    async def upsert_local_agent(
        self,
        discovery_url: str,
        card: AgentCardSnapshot,
    ) -> LocalAgentUpsertResult: ...

    async def list_local_agent_ids(self) -> list[str]: ...

    async def mark_local_agents_inactive(self, agent_ids: list[str]) -> int: ...


class LocalAgentDiscovery(Protocol):
    async def request_discovery(
        self,
        trigger: DiscoveryTrigger = DiscoveryTrigger.MANUAL,
    ) -> LocalAgentDiscoveryResult: ...


__all__ = ["LocalAgentDiscovery", "LocalAgentWriter"]
