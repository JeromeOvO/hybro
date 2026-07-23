import pytest

from common.dto import VectorSearchResult
from common.dto.agent import AgentCardSnapshot
from tests.test_agent_facade import _facade_with_docs


@pytest.mark.asyncio
async def test_agent_golden_register_list_match_and_direct_callability():
    card = AgentCardSnapshot(
        agent_id="snapshot",
        name="Writer",
        description="Writes stories",
        url="https://writer.example",
        raw_card={
            "name": "Writer",
            "description": "Writes stories",
            "url": "https://writer.example",
            "defaultInputModes": ["text"],
        },
    )
    facade, repo, vector, _, _ = _facade_with_docs([], resolved_card=card)

    registered = await facade.register_agent("https://writer.example", "u1")
    public = await facade.list_public_agents()
    owned = await facade.list_agents("u1")

    vector.results = [VectorSearchResult(id=registered.agent_id, score=0.90)]
    matched = await facade.match_agents("write a story", requesting_user_id=None)

    assert registered.agent_id == "new-agent"
    assert repo.docs["new-agent"]["normalized_url"] == "https://writer.example"
    assert public[0].agent_id == "new-agent"
    assert owned[0].agent_id == "new-agent"
    assert matched[0].agent_id == "new-agent"
    assert matched[0].agent.url == "https://writer.example"
    assert await facade.is_agent_healthy("new-agent") is True
    assert await facade.is_directly_callable("new-agent") is True


@pytest.mark.asyncio
async def test_agent_golden_private_and_inactive_agents_do_not_leak_to_discovery():
    facade, _, vector, llm, _ = _facade_with_docs(
        [
            {
                "agent_id": "public",
                "is_public": True,
                "agent_status": "active",
                "agent_card": {"name": "Public", "url": "https://public"},
            },
            {
                "agent_id": "private",
                "provider_id": "u1",
                "is_public": False,
                "agent_status": "active",
                "agent_card": {"name": "Private", "url": "https://private"},
            },
            {
                "agent_id": "inactive",
                "is_public": True,
                "agent_status": "inactive",
                "agent_card": {"name": "Inactive", "url": "https://inactive"},
            },
        ]
    )
    vector.results = [
        VectorSearchResult(id="private", score=0.99),
        VectorSearchResult(id="inactive", score=0.98),
        VectorSearchResult(id="public", score=0.70),
    ]

    unauthenticated = await facade.match_agents("query", requesting_user_id=None)
    filtered_empty = await facade.match_agents("query", filter_ids=[])

    assert [match.agent_id for match in unauthenticated] == ["public"]
    assert filtered_empty == []
    assert llm.embedded == ["query"]
