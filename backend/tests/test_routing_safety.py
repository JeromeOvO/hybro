"""Routing eligibility for agents loaded from stored documents."""

from __future__ import annotations

import pytest

from agent.repository.documents import _safe_parse_agent
from agent.routing_safety import sanitize_routing_agent_ids


@pytest.mark.asyncio
async def test_stored_agent_without_skills_is_routable():
    # Registration stores cards via ProtoJSON, which omits an empty skills list.
    stored = {
        "agent_id": "agent-without-skills",
        "agent_card": {
            "name": "agent",
            "description": "desc",
            "url": "https://agent.example",
            "version": "1.0.0",
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
        },
        "agent_status": "active",
        "is_public": True,
        "provider_id": None,
    }

    async def lookup(agent_id: str):
        return _safe_parse_agent(stored) if agent_id == stored["agent_id"] else None

    agents, rejected = await sanitize_routing_agent_ids(
        ["agent-without-skills"],
        lookup=lookup,
        user_id="user_local_developer",
    )

    assert [agent.agent_id for agent in agents] == ["agent-without-skills"]
    assert rejected == []
