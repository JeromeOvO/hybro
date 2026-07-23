from datetime import UTC, datetime

import pytest

from agent.facade import AgentFacade
from common.dto.agent import AgentCardSnapshot
from tests.test_agent_facade import Repository, _doc


class CardResolver:
    def __init__(self, card: AgentCardSnapshot):
        self.card = card

    async def resolve_card(self, _url):
        return self.card


class GoldenRepository(Repository):
    async def find_by_normalized_url(self, normalized_url, provider_id=None):
        del provider_id
        return next(
            (
                doc
                for doc in self.docs.values()
                if doc.get("normalized_url") == normalized_url
            ),
            None,
        )

    async def public_url_exists(self, _subdomain, _base_domain):
        return False

    async def upsert(self, agent_id, doc):
        self.docs[agent_id] = doc

    async def get_public(self, limit=50):
        return [
            doc for doc in self.docs.values() if doc.get("is_public", True)
        ][:limit]

    async def get_by_provider(self, provider_id):
        return [
            doc
            for doc in self.docs.values()
            if doc.get("provider_id") == provider_id
        ]


def _golden_facade(repo, card):
    return AgentFacade(
        repository=repo,
        card_resolver=CardResolver(card),
        id_factory=lambda: "new-agent",
        now=lambda: datetime.now(UTC),
    )


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
    repo = GoldenRepository([])
    facade = _golden_facade(repo, card)

    registered = await facade.register_agent("https://writer.example", "u1")
    public = await facade.list_public_agents()
    owned = await facade.list_agents("u1")
    matched = await facade.match_agents("write stories", requesting_user_id=None)

    assert registered.agent_id == "new-agent"
    assert repo.docs["new-agent"]["normalized_url"] == "https://writer.example"
    assert public[0].agent_id == "new-agent"
    assert owned[0].agent_id == "new-agent"
    assert matched[0].agent_id == "new-agent"
    assert matched[0].agent.url == "https://writer.example"
    assert matched[0].score > 0
    assert await facade.is_agent_healthy("new-agent") is True
    assert await facade.is_directly_callable("new-agent") is True


@pytest.mark.asyncio
async def test_agent_golden_private_and_inactive_agents_do_not_leak_to_discovery():
    repo = GoldenRepository(
        [
            _doc("public", "Public", description="query"),
            _doc(
                "private",
                "Private",
                description="query",
                provider_id="u1",
                public=False,
            ),
            _doc("inactive", "Inactive", description="query", active=False),
        ],
        text_rows=[
            {"agent_id": "private", "score": 99},
            {"agent_id": "inactive", "score": 98},
            {"agent_id": "public", "score": 1},
        ],
    )
    facade = _golden_facade(
        repo,
        AgentCardSnapshot(
            agent_id="unused",
            name="unused",
            url="https://unused.example",
            raw_card={},
        ),
    )

    unauthenticated = await facade.match_agents("query", requesting_user_id=None)
    filtered_empty = await facade.match_agents("query", filter_ids=[])

    assert [match.agent_id for match in unauthenticated] == ["public"]
    assert repo.text_search_calls == [["public"]]
    assert filtered_empty == []
