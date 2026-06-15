from __future__ import annotations

from types import SimpleNamespace

import pytest

from hub_runtime_bridge.service.agent_sync import HubAgentSyncService


class Writer:
    def __init__(self) -> None:
        self.calls = []

    async def sync_hub_agents(self, hub_id, owner_user_id, agents, prune_missing=True):
        self.calls.append((hub_id, owner_user_id, agents, prune_missing))
        return [
            SimpleNamespace(agent_id=f"cloud-{agent.agent_id}", descriptor=agent)
            for agent in agents
        ]


@pytest.mark.asyncio
async def test_agent_sync_filters_invalid_cards_and_uses_writer_protocol() -> None:
    writer = Writer()
    service = HubAgentSyncService(writer=writer)
    valid = SimpleNamespace(
        local_agent_id="local-1",
        name="Agent",
        capabilities=[],
        agent_card={
            "name": "Agent",
            "description": "desc",
            "url": "https://example.com",
            "version": "1.0.0",
            "capabilities": {},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        },
    )
    invalid = SimpleNamespace(
        local_agent_id="local-2", name="Bad", capabilities=[], agent_card={}
    )

    synced = await service.sync_agents("hub-1", "owner-1", [valid, invalid])

    assert synced == [{"agent_id": "cloud-local-1", "local_agent_id": "local-1"}]
    assert writer.calls[0][0:2] == ("hub-1", "owner-1")
