from __future__ import annotations

from common.dto import HubAgentDescriptor
from hub_runtime_bridge.adapters.a2a_card import is_valid_agent_card


class HubAgentSyncService:
    def __init__(self, *, writer, streams=None) -> None:
        self._writer = writer
        self._streams = streams

    async def sync_agents(
        self, hub_id: str, owner_id: str, agents: list, *, prune_missing: bool = True
    ) -> list[dict]:
        if self._streams:
            await self._streams.record_heartbeat(hub_id)
        valid = [
            agent
            for agent in agents
            if is_valid_agent_card(getattr(agent, "agent_card", {}) or {})
        ]
        if agents and not valid:
            return []
        descriptors = [
            HubAgentDescriptor(
                hub_id=hub_id,
                agent_id=agent.local_agent_id,
                name=agent.name,
                url=(agent.agent_card or {}).get("url"),
                capabilities=list(agent.capabilities or []),
                raw_card=dict(agent.agent_card or {}),
            )
            for agent in valid
        ]
        synced = await self._writer.sync_hub_agents(
            hub_id, owner_id, descriptors, prune_missing=prune_missing
        )
        return [
            {
                "agent_id": item.agent_id,
                "local_agent_id": item.descriptor.agent_id if item.descriptor else None,
            }
            for item in synced
        ]


__all__ = ["HubAgentSyncService"]
