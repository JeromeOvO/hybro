from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from common.dto import VectorSearchResult
from common.dto.agent import AgentCardSnapshot, HubAgentDescriptor


def test_url_normalization_strips_well_known_paths_and_default_ports():
    from agent.url_utils import is_local_agent_url, normalize_agent_url

    assert (
        normalize_agent_url("http://127.0.0.1:80/.well-known/agent.json")
        == "http://localhost"
    )
    assert normalize_agent_url("https://EXAMPLE.com:443/path/") == "https://example.com/path"
    assert (
        normalize_agent_url("https://example.com/.well-known/agent-card.json?token=1")
        == "https://example.com?token=1"
    )
    assert is_local_agent_url("http://localhost:8000") is True
    assert is_local_agent_url("http://127.0.0.1:8000") is True
    assert is_local_agent_url("http://[::1]:8000") is True
    assert is_local_agent_url("http://0.0.0.0:8000") is True
    assert is_local_agent_url("https://example.com") is False


@pytest.mark.asyncio
async def test_public_url_generation_prefers_available_subdomain_and_rejects_reserved():
    from agent.public_url import PublicUrlGenerator

    taken = {"api", "writer"}

    async def exists(subdomain: str, base_domain: str) -> bool:
        assert base_domain == "hybro.ai"
        return subdomain in taken

    generator = PublicUrlGenerator(
        exists=exists,
        base_domain="hybro.ai",
        protocol="https",
        id_factory=lambda: "fallback-id",
    )

    assert await generator.generate_public_url(
        agent_name="Story Agent",
        agent_id="a1",
        preferred_subdomain="My Custom AI Bot",
    ) == "https://mycustom.hybro.ai"

    assert await generator.generate_public_url(
        agent_name="Writer",
        agent_id="a1",
        preferred_subdomain="api",
    ) == "https://writer-f55ff1.hybro.ai"


@pytest.mark.asyncio
async def test_public_url_generation_uses_uuid_fallback_when_hash_is_taken():
    from agent.public_url import PublicUrlGenerator

    taken = {"writer", "writer-f55ff1"}

    async def exists(subdomain: str, base_domain: str) -> bool:
        return subdomain in taken

    generator = PublicUrlGenerator(
        exists=exists,
        base_domain="hybro.ai",
        protocol="https",
        id_factory=lambda: "12345678abcdef",
    )

    assert await generator.generate_public_url(
        agent_name="The Writer AI Bot!",
        agent_id="a1",
    ) == "https://writer-12345678.hybro.ai"


def test_public_url_normalization_only_strips_standalone_ai():
    from agent.public_url import normalize_subdomain

    assert normalize_subdomain("Aiden") == "aiden"
    assert normalize_subdomain("Maintenance") == "maintenance"
    assert normalize_subdomain("AI Assistant") == "assistant"
    assert normalize_subdomain("my ai bot") == "my"


def test_matching_helpers_score_capabilities_and_cutoffs():
    from agent.matching import (
        compute_capability_score,
        compute_final_score,
        select_top_matches,
        supports_files,
    )

    text_agent = {"agent_card": {"defaultInputModes": ["text"]}}
    file_agent = {"agent_card": {"defaultInputModes": ["image/png"]}}
    wildcard_agent = {"agent_card": {"default_input_modes": ["*/*"]}}
    pdf_agent = {"agent_card": {"defaultInputModes": ["application/pdf"]}}

    assert supports_files(file_agent) is True
    assert supports_files(wildcard_agent) is True
    assert supports_files(pdf_agent) is True
    assert supports_files(text_agent) is False
    assert compute_capability_score(text_agent) == 1.0
    assert compute_capability_score(text_agent, ["image/png"]) == 0.0
    assert compute_capability_score(file_agent, ["image/png"]) == 1.0
    assert compute_final_score(0.8, 1.0) == pytest.approx(0.83)

    close_ranked = [
        {"agent_id": "a1", "final_score": 0.80},
        {"agent_id": "a2", "final_score": 0.72},
        {"agent_id": "a3", "final_score": 0.50},
        {"agent_id": "a4", "final_score": 0.30},
    ]
    clear_ranked = [
        {"agent_id": "a1", "final_score": 0.90},
        {"agent_id": "a2", "final_score": 0.60},
    ]
    low_debate = [
        {"agent_id": "a1", "final_score": 0.20},
        {"agent_id": "a2", "final_score": 0.10},
    ]

    assert [m["agent_id"] for m in select_top_matches(clear_ranked)] == ["a1"]
    assert [m["agent_id"] for m in select_top_matches(close_ranked)] == ["a1", "a2", "a3"]
    assert [m["agent_id"] for m in select_top_matches(close_ranked, is_debate_mode=True)] == [
        "a1",
        "a2",
        "a3",
    ]
    assert [m["agent_id"] for m in select_top_matches(low_debate, is_debate_mode=True)] == [
        "a1",
        "a2",
    ]


def test_translators_convert_mongo_dicts_to_common_dtos():
    from agent.translators import agent_card_from_doc, agent_info_from_doc

    doc = {
        "agent_id": "a1",
        "provider_id": "u1",
        "agent_status": "inactive",
        "is_public": False,
        "source": "hub",
        "hub_id": "hub-1",
        "is_hub_online": True,
        "public_url": "https://a1.hybro.ai",
        "capabilities": ["search"],
        "rate_limit_per_user_per_hour": 3,
        "agent_card": {
            "name": "Search Agent",
            "description": "Finds things",
            "url": "https://real.example",
            "defaultInputModes": ["text"],
        },
    }

    info = agent_info_from_doc(doc)
    card = agent_card_from_doc(doc)

    assert info.agent_id == "a1"
    assert info.name == "Search Agent"
    assert info.status == "inactive"
    assert info.url == "https://real.example"
    assert info.is_public is False
    assert info.public_url == "https://a1.hybro.ai"
    assert info.rate_limit_per_user_per_hour == 3
    assert card == AgentCardSnapshot(
        agent_id="a1",
        name="Search Agent",
        description="Finds things",
        url="https://real.example",
        capabilities=["search"],
        raw_card=doc["agent_card"],
    )


def test_translators_build_registration_hub_docs_and_order_results():
    from agent.translators import (
        docs_by_vector_order,
        hub_descriptor_to_doc,
        registration_doc_from_card,
    )

    now = datetime(2026, 5, 10, tzinfo=UTC)
    card = AgentCardSnapshot(
        agent_id="snapshot",
        name="Writer",
        description="Writes",
        url="https://writer.example",
        capabilities=["write"],
        raw_card={"name": "Writer", "description": "Writes", "url": "https://writer.example"},
    )
    doc = registration_doc_from_card(
        agent_id="a1",
        provider_id="u1",
        card=card,
        normalized_url="https://writer.example",
        public_url="https://writer.hybro.ai",
        now=now,
        is_public=False,
        rate_limit_per_user_per_hour=10,
    )

    assert doc["agent_id"] == "a1"
    assert doc["agent_status"] == "active"
    assert doc["normalized_url"] == "https://writer.example"
    assert doc["agent_card"] == card.raw_card
    assert doc["created_at"] == now

    descriptor = HubAgentDescriptor(
        hub_id="ignored",
        agent_id="local-1",
        name="Hub Writer",
        url="http://localhost:9000",
        capabilities=["write"],
        raw_card={"name": "Hub Writer", "url": "http://localhost:9000", "description": "Hub"},
    )
    hub_doc = hub_descriptor_to_doc(
        hub_id="hub-1",
        owner_user_id="u1",
        descriptor=descriptor,
        agent_id="a2",
        normalized_url=None,
        public_url="https://gateway/gateway/agents/a2/message/send",
    )

    assert hub_doc["source"] == "hub"
    assert hub_doc["local_agent_id"] == "local-1"
    assert hub_doc["is_public"] is False
    assert hub_doc["agent_card"]["name"] == "Hub Writer"
    assert docs_by_vector_order(
        [{"agent_id": "a2"}, {"agent_id": "a1"}],
        ["a1", "a2"],
    ) == [{"agent_id": "a1"}, {"agent_id": "a2"}]


@pytest.mark.asyncio
async def test_facade_sync_hub_agents_enriches_existing_and_upserts_new_agents():
    existing_hash = hashlib.sha256(b"Existing description").hexdigest()
    facade, repo, vector, llm, hub = _facade_with_docs(
        [
            {
                "agent_id": "existing",
                "provider_id": "u1",
                "is_public": True,
                "normalized_url": "https://existing.example",
                "description_hash": existing_hash,
                "agent_card": {
                    "name": "Existing",
                    "description": "Existing description",
                    "url": "https://existing.example",
                    "iconUrl": "https://assets.example/custom.png",
                },
            }
        ],
        hub_online={"hub-1": True},
        gateway_base_url="https://gateway.example",
    )
    descriptors = [
        HubAgentDescriptor(
            hub_id="hub-1",
            agent_id="local-existing",
            name="Existing From Hub",
            url="https://existing.example",
            capabilities=["search"],
            raw_card={
                "name": "Existing From Hub",
                "description": "Existing description",
                "url": "https://existing.example",
            },
        ),
        HubAgentDescriptor(
            hub_id="hub-1",
            agent_id="local-new",
            name="Local Agent",
            url="http://localhost:9000",
            capabilities=["local"],
            raw_card={
                "name": "Local Agent",
                "description": "Local description",
                "url": "http://localhost:9000",
            },
        ),
        HubAgentDescriptor(hub_id="hub-1", agent_id="invalid", raw_card={}),
    ]

    synced = await facade.sync_hub_agents(
        "hub-1",
        "u1",
        descriptors,
        prune_missing=True,
    )

    assert [item.agent_id for item in synced] == ["existing", "new-agent"]
    assert synced[0].is_online is True
    assert repo.docs["existing"]["source"] == "hub"
    assert repo.docs["existing"]["local_agent_id"] == "local-existing"
    assert repo.docs["existing"]["agent_card"]["name"] == "Existing From Hub"
    assert repo.docs["existing"]["agent_card"]["iconUrl"] == (
        "https://assets.example/custom.png"
    )
    assert repo.docs["existing"]["is_public"] is True
    assert repo.docs["new-agent"]["normalized_url"] is None
    assert repo.docs["new-agent"]["is_public"] is False
    assert repo.docs["new-agent"]["public_url"] == (
        "https://gateway.example/gateway/agents/new-agent/message/send"
    )
    assert repo.prune_calls == [("hub-1", ["existing", "new-agent"])]
    assert repo.activate_calls == [["existing", "new-agent"]]
    assert hub.checked == ["hub-1"]
    assert llm.embedded == ["Local description"]
    assert vector.upserts[0][1][0].id == "new-agent"
    assert await repo.get_indexed_description_hash("new-agent") == hashlib.sha256(
        b"Local description"
    ).hexdigest()


@pytest.mark.asyncio
async def test_facade_sync_hub_agents_uses_async_liveness_for_online_activation():
    facade, repo, _, _, hub = _facade_with_docs([], hub_online={"hub-1": True})

    synced = await facade.sync_hub_agents(
        "hub-1",
        "u1",
        [
            HubAgentDescriptor(
                hub_id="hub-1",
                agent_id="local-new",
                raw_card={
                    "name": "Local Agent",
                    "description": "Local description",
                    "url": "http://localhost:9000",
                },
            )
        ],
        prune_missing=False,
    )

    assert [item.agent_id for item in synced] == ["new-agent"]
    assert synced[0].is_online is True
    assert repo.activate_calls == [["new-agent"]]
    assert hub.checked == ["hub-1"]


@pytest.mark.asyncio
async def test_facade_sync_hub_agents_reuses_stored_id_for_local_proxy_url():
    facade, repo, _, _, _ = _facade_with_docs(
        [
            {
                "agent_id": "existing-local",
                "provider_id": "u1",
                "source": "hub",
                "hub_id": "hub-1",
                "local_agent_id": "local-1",
                "agent_status": "active",
                "agent_card": {
                    "name": "Local Agent",
                    "description": "Old",
                    "url": "http://localhost:9000",
                },
            }
        ],
        gateway_base_url="https://gateway.example",
    )

    synced = await facade.sync_hub_agents(
        "hub-1",
        "u1",
        [
            HubAgentDescriptor(
                hub_id="hub-1",
                agent_id="local-1",
                raw_card={
                    "name": "Local Agent",
                    "description": "New",
                    "url": "http://localhost:9000",
                },
            )
        ],
    )

    assert [item.agent_id for item in synced] == ["existing-local"]
    assert repo.docs["existing-local"]["public_url"] == (
        "https://gateway.example/gateway/agents/existing-local/message/send"
    )
    assert "new-agent" not in repo.docs


@pytest.mark.asyncio
async def test_facade_sync_hub_agents_preserves_existing_public_url_without_gateway():
    facade, repo, _, _, _ = _facade_with_docs(
        [
            {
                "agent_id": "existing",
                "provider_id": "u1",
                "source": "self",
                "is_public": True,
                "public_url": "https://writer.hybro.ai",
                "normalized_url": "https://existing.example",
                "agent_card": {
                    "name": "Existing",
                    "description": "Existing description",
                    "url": "https://existing.example",
                },
            }
        ],
    )

    synced = await facade.sync_hub_agents(
        "hub-1",
        "u1",
        [
            HubAgentDescriptor(
                hub_id="hub-1",
                agent_id="local-existing",
                raw_card={
                    "name": "Existing From Hub",
                    "description": "Updated description",
                    "url": "https://existing.example",
                },
            )
        ],
    )

    assert [item.agent_id for item in synced] == ["existing"]
    assert repo.docs["existing"]["public_url"] == "https://writer.hybro.ai"


@pytest.mark.asyncio
async def test_facade_sync_hub_agents_empty_inventory_prunes_all_hub_agents():
    facade, repo, _, _, _ = _facade_with_docs([
        {
            "agent_id": "old-hub-agent",
            "provider_id": "u1",
            "source": "hub",
            "hub_id": "hub-1",
            "local_agent_id": "local-old",
            "agent_status": "active",
            "agent_card": {"name": "Old", "url": "https://old.example"},
        }
    ])

    synced = await facade.sync_hub_agents("hub-1", "u1", [], prune_missing=True)

    assert synced == []
    assert repo.prune_calls == [("hub-1", [])]
    assert repo.docs["old-hub-agent"]["agent_status"] == "inactive"


@pytest.mark.asyncio
async def test_facade_sync_hub_agents_skips_prune_when_all_descriptors_invalid():
    facade, repo, vector, llm, _ = _facade_with_docs(
        [{"agent_id": "old", "hub_id": "hub-1", "source": "hub", "agent_status": "active"}]
    )

    synced = await facade.sync_hub_agents(
        "hub-1",
        "u1",
        [HubAgentDescriptor(hub_id="hub-1", agent_id="invalid", raw_card={})],
        prune_missing=True,
    )

    assert synced == []
    assert repo.prune_calls == []
    assert vector.upserts == []
    assert llm.embedded == []


@pytest.mark.asyncio
async def test_facade_mark_hub_agents_offline_delegates_to_repository():
    facade, repo, _, _, _ = _facade_with_docs([
        {"agent_id": "hub", "hub_id": "hub-1", "agent_status": "active"}
    ])

    await facade.mark_hub_agents_offline("hub-1")

    assert repo.offline_calls == ["hub-1"]
    assert repo.docs["hub"]["agent_status"] == "inactive"


@pytest.mark.asyncio
async def test_facade_match_returns_empty_without_candidates_or_empty_filter():
    facade, repo, vector, llm, _ = _facade_with_docs([
        {
            "agent_id": "private",
            "provider_id": "u2",
            "is_public": False,
            "agent_status": "active",
            "agent_card": {"name": "Private", "url": "https://p"},
        }
    ])

    assert await facade.match_agents("hello", filter_ids=[]) == []
    assert await facade.match_agents("hello", requesting_user_id=None) == []
    assert llm.embedded == []
    assert vector.searches == []
    assert repo.list_visible_calls[-1]["user_id"] is None


@pytest.mark.asyncio
async def test_facade_match_applies_visibility_filter_and_returns_agent_results():
    facade, repo, vector, llm, _ = _facade_with_docs([
        {
            "agent_id": "public",
            "provider_id": "u2",
            "is_public": True,
            "agent_status": "active",
            "agent_card": {
                "name": "Public",
                "description": "General",
                "url": "https://public",
                "defaultInputModes": ["text"],
            },
        },
        {
            "agent_id": "owned-private",
            "provider_id": "u1",
            "is_public": False,
            "agent_status": "active",
            "agent_card": {
                "name": "Owned",
                "description": "Images",
                "url": "https://owned",
                "defaultInputModes": ["image/png"],
            },
        },
        {
            "agent_id": "other-private",
            "provider_id": "u2",
            "is_public": False,
            "agent_status": "active",
            "agent_card": {"name": "Other", "url": "https://other"},
        },
        {
            "agent_id": "inactive",
            "is_public": True,
            "agent_status": "inactive",
            "agent_card": {"name": "Inactive", "url": "https://inactive"},
        },
    ])
    vector.results = [
        VectorSearchResult(id="other-private", score=0.99),
        VectorSearchResult(id="owned-private", score=0.80),
        VectorSearchResult(id="public", score=0.79),
        VectorSearchResult(id="inactive", score=0.98),
    ]

    matches = await facade.match_agents(
        "image task",
        limit=5,
        filter_ids=["public", "owned-private", "other-private", "inactive"],
        requesting_user_id="u1",
    )

    assert [match.agent_id for match in matches] == ["owned-private", "public"]
    assert matches[0].agent.name == "Owned"
    assert matches[0].score == pytest.approx(0.83)
    assert llm.embedded == ["image task"]
    assert vector.searches == [
        {
            "index": "a2a-agents",
            "vector": [0.1, 0.2, 0.3],
            "top_k": 15,
            "filter": {"agent_id": {"$in": ["public", "owned-private"]}},
        }
    ]
    assert repo.list_visible_calls[-1] == {
        "user_id": "u1",
        "active_only": True,
        "agent_ids": ["public", "owned-private", "other-private", "inactive"],
        "query": None,
        "limit": 0,
    }


@pytest.mark.asyncio
async def test_facade_match_excludes_agents_with_open_capability_issues():
    facade, _, vector, _, _ = _facade_with_docs(
        [
            {
                "agent_id": "good",
                "is_public": True,
                "agent_status": "active",
                "agent_card": {"name": "Good", "url": "https://good"},
            },
            {
                "agent_id": "bad",
                "is_public": True,
                "agent_status": "active",
                "agent_card": {"name": "Bad", "url": "https://bad"},
            },
        ],
        exclusion_reader=FakeExclusionReader({"bad"}),
    )
    vector.results = [
        VectorSearchResult(id="bad", score=0.99),
        VectorSearchResult(id="good", score=0.60),
    ]

    matches = await facade.match_agents("query", limit=5)

    assert [match.agent_id for match in matches] == ["good"]
    assert vector.searches[-1]["filter"] == {"agent_id": {"$in": ["good"]}}


@pytest.mark.asyncio
async def test_facade_match_prefilters_attachment_compatible_vector_candidates():
    class CrowdedVector(FakeVector):
        async def search(self, index, vector, top_k, filter=None):
            self.searches.append(
                {"index": index, "vector": vector, "top_k": top_k, "filter": filter}
            )
            allowed = set((filter or {}).get("agent_id", {}).get("$in", []))
            ordered_ids = [f"text-{index:02d}" for index in range(16)] + ["pdf-agent"]
            return [
                VectorSearchResult(
                    id=agent_id,
                    score=1.0 if agent_id.startswith("text") else 0.2,
                )
                for agent_id in ordered_ids
                if agent_id in allowed
            ][:top_k]

    text_agents = [
        {
            "agent_id": f"text-{index:02d}",
            "is_public": True,
            "agent_status": "active",
            "agent_card": {
                "name": f"Text {index}",
                "url": f"https://text-{index}.example",
                "defaultInputModes": ["text"],
            },
        }
        for index in range(16)
    ]
    pdf_agent = {
        "agent_id": "pdf-agent",
        "is_public": True,
        "agent_status": "active",
        "agent_card": {
            "name": "PDF Agent",
            "url": "https://pdf.example",
            "defaultInputModes": ["application/pdf"],
        },
    }
    facade, _, vector, _, _ = _facade_with_docs(
        [*text_agents, pdf_agent],
        vector=CrowdedVector(),
    )

    matches = await facade.match_for_message(
        "summarize this attachment",
        limit=1,
        required_input_modes=["application/pdf"],
    )

    assert [match["agent_id"] for match in matches] == ["pdf-agent"]
    assert vector.searches[-1]["filter"] == {"agent_id": {"$in": ["pdf-agent"]}}


@pytest.mark.asyncio
async def test_facade_match_falls_back_to_visible_agents_when_vector_index_missing():
    from common.errors import VectorIndexUnavailableError

    class MissingIndexVector(FakeVector):
        async def search(self, index, vector, top_k, filter=None):
            self.searches.append(
                {"index": index, "vector": vector, "top_k": top_k, "filter": filter}
            )
            raise VectorIndexUnavailableError(index, "search")

    facade, repo, vector, llm, _ = _facade_with_docs(
        [
            {
                "agent_id": "public",
                "is_public": True,
                "agent_status": "active",
                "agent_card": {"name": "Public", "url": "https://public"},
            },
            {
                "agent_id": "owned-private",
                "provider_id": "u1",
                "is_public": False,
                "agent_status": "active",
                "agent_card": {"name": "Owned", "url": "https://owned"},
            },
            {
                "agent_id": "other-private",
                "provider_id": "u2",
                "is_public": False,
                "agent_status": "active",
                "agent_card": {"name": "Other", "url": "https://other"},
            },
        ],
        vector=MissingIndexVector(),
    )

    matches = await facade.match_agents(
        "hello",
        limit=5,
        requesting_user_id="u1",
    )

    assert [match.agent_id for match in matches] == ["public"]
    assert llm.embedded == ["hello"]
    assert vector.searches == [
        {
            "index": "a2a-agents",
            "vector": [0.1, 0.2, 0.3],
            "top_k": 15,
            "filter": {"agent_id": {"$in": ["public", "owned-private"]}},
        }
    ]
    assert repo.get_by_ids_calls == []


@pytest.mark.asyncio
async def test_facade_match_respect_visibility_false_still_excludes_private_agents():
    facade, _, vector, _, _ = _facade_with_docs([
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
    ])
    vector.results = [
        VectorSearchResult(id="private", score=0.99),
        VectorSearchResult(id="public", score=0.60),
    ]

    matches = await facade.match_agents(
        "query",
        respect_visibility=False,
        requesting_user_id="u1",
    )

    assert [match.agent_id for match in matches] == ["public"]


@pytest.mark.asyncio
async def test_facade_registers_agent_and_indexes_description():
    card = AgentCardSnapshot(
        agent_id="snapshot",
        name="Writer Agent",
        description="Writes prose",
        url="https://writer.example/.well-known/agent.json",
        capabilities=["write"],
        raw_card={
            "name": "Writer Agent",
            "description": "Writes prose",
            "url": "https://writer.example/.well-known/agent.json",
        },
    )
    facade, repo, vector, llm, _ = _facade_with_docs([], resolved_card=card)

    agent = await facade.register_agent(
        "https://writer.example/.well-known/agent.json",
        "u1",
        preferred_subdomain="Writer Bot",
        is_public=False,
        rate_limit_per_user_per_hour=10,
    )

    assert agent.agent_id == "new-agent"
    assert agent.name == "Writer Agent"
    assert agent.is_public is False
    assert repo.find_normalized_calls == [("https://writer.example", None)]
    assert repo.docs["new-agent"]["normalized_url"] == "https://writer.example"
    assert repo.docs["new-agent"]["public_url"] == "https://writer.hybro.ai"
    assert repo.docs["new-agent"]["rate_limit_per_user_per_hour"] == 10
    assert llm.embedded == ["Writes prose"]
    assert vector.upserts[0][0] == "a2a-agents"
    assert vector.upserts[0][1][0].id == "new-agent"
    assert vector.upserts[0][1][0].metadata == {
        "type": "a2a_agent",
        "agent_id": "new-agent",
    }


@pytest.mark.asyncio
async def test_facade_register_rejects_missing_duplicate_unresolved_and_rolls_back():
    card = AgentCardSnapshot(
        agent_id="snapshot",
        name="Writer",
        description="Writes",
        url="https://writer.example",
        raw_card={"name": "Writer", "description": "Writes", "url": "https://writer.example"},
    )
    facade, repo, vector, _, _ = _facade_with_docs(
        [{"agent_id": "existing", "normalized_url": "https://writer.example"}],
        resolved_card=card,
    )

    with pytest.raises(ValueError, match="url is required"):
        await facade.register_agent("", "u1")
    with pytest.raises(ValueError, match="already registered"):
        await facade.register_agent("https://writer.example", "u1")

    unresolved, unresolved_repo, _, _, _ = _facade_with_docs([], resolved_card=None)
    with pytest.raises(ValueError, match="could not resolve"):
        await unresolved.register_agent("https://missing.example", "u1")
    assert unresolved_repo.docs == {}

    vector.fail_upsert = True
    rollback, rollback_repo, _, _, _ = _facade_with_docs([], resolved_card=card, vector=vector)
    with pytest.raises(RuntimeError, match="vector failed"):
        await rollback.register_agent("https://writer.example", "u1")
    assert "new-agent" not in rollback_repo.docs
    assert rollback_repo.deleted == ["new-agent"]


@pytest.mark.asyncio
async def test_facade_sync_hub_agents_does_not_abort_when_indexing_fails():
    vector = FakeVector()
    vector.fail_upsert = True
    facade, repo, _, _, _ = _facade_with_docs(
        [],
        vector=vector,
        gateway_base_url="https://gateway.example",
    )

    synced = await facade.sync_hub_agents(
        "hub-1",
        "u1",
        [
            HubAgentDescriptor(
                hub_id="hub-1",
                agent_id="local-1",
                raw_card={
                    "name": "Local",
                    "description": "Index me",
                    "url": "http://localhost:9000",
                },
            )
        ],
    )

    assert [item.agent_id for item in synced] == ["new-agent"]
    assert repo.docs["new-agent"]["agent_status"] == "active"
    assert await repo.get_indexed_description_hash("new-agent") is None


@pytest.mark.asyncio
async def test_facade_delete_enforces_owner_and_deletes_vector_record():
    facade, repo, vector, _, _ = _facade_with_docs([
        {"agent_id": "a1", "provider_id": "u1", "agent_card": {"url": "https://a1"}},
        {"agent_id": "a2", "provider_id": "u2", "agent_card": {"url": "https://a2"}},
    ])

    assert await facade.delete_agent("missing", "u1") is False
    assert await facade.delete_agent("a2", "u1") is False
    assert await facade.delete_agent("a1", "u1") is True
    assert "a1" not in repo.docs
    assert vector.deletes == [("a2a-agents", ["a1"])]


@pytest.mark.asyncio
async def test_facade_delete_returns_success_when_vector_cleanup_fails_after_db_delete():
    vector = FakeVector()
    vector.fail_delete = True
    facade, repo, vector, _, _ = _facade_with_docs(
        [{"agent_id": "a1", "provider_id": "u1", "agent_card": {"url": "https://a1"}}],
        vector=vector,
    )

    assert await facade.delete_agent("a1", "u1") is True
    assert "a1" not in repo.docs
    assert vector.deletes == [("a2a-agents", ["a1"])]


@pytest.mark.asyncio
async def test_facade_update_validates_rate_limits_and_reindexes_card_description():
    facade, repo, vector, llm, _ = _facade_with_docs([
        {
            "agent_id": "a1",
            "provider_id": "u1",
            "agent_status": "active",
            "agent_card": {
                "name": "Writer",
                "description": "Old",
                "url": "https://writer.example",
                "iconUrl": "managed",
            },
        }
    ])

    assert await facade.update_agent("missing", {"is_public": False}) is None
    with pytest.raises(ValueError, match="rate_limit_per_user_per_hour"):
        await facade.update_agent("a1", {"rate_limit_per_user_per_hour": -1})
    with pytest.raises(ValueError, match="Unknown agent update keys"):
        await facade.update_agent("a1", {"provider_id": "u2"})

    updated = await facade.update_agent(
        "a1",
        {
            "is_public": False,
            "agent_status": "inactive",
            "rate_limit_per_user_per_hour": 5,
            "agent_card": {"description": "New", "iconUrl": "external"},
        },
    )

    assert updated.status == "inactive"
    assert updated.is_public is False
    assert repo.docs["a1"]["agent_card"]["description"] == "New"
    assert repo.docs["a1"]["agent_card"]["iconUrl"] == "managed"
    assert llm.embedded == ["New"]
    assert vector.upserts[0][1][0].id == "a1"


@pytest.mark.asyncio
async def test_facade_lists_owned_and_public_agents_with_hub_liveness():
    facade, _, _, _, hub = _facade_with_docs(
        [
            {
                "agent_id": "owned",
                "provider_id": "u1",
                "is_public": False,
                "agent_card": {"name": "Owned", "url": "https://o"},
            },
            {"agent_id": "public", "is_public": True, "agent_card": {"name": "Public", "url": "https://p"}},
            {
                "agent_id": "hub",
                "is_public": True,
                "source": "hub",
                "hub_id": "hub-1",
                "agent_card": {"name": "Hub", "url": "https://h"},
            },
            {
                "agent_id": "hub-same",
                "is_public": True,
                "source": "hub",
                "hub_id": "hub-1",
                "agent_card": {"name": "Same Hub", "url": "https://h-same"},
            },
            {
                "agent_id": "hub-offline",
                "is_public": True,
                "source": "hub",
                "hub_id": "hub-2",
                "agent_card": {"name": "Offline Hub", "url": "https://h2"},
            },
        ],
        hub_online={"hub-1": True, "hub-2": False},
    )

    owned = await facade.list_agents("u1")
    public = await facade.list_public_agents(limit=10)

    assert [agent.agent_id for agent in owned] == ["owned"]
    assert [agent.agent_id for agent in public] == [
        "public",
        "hub",
        "hub-same",
        "hub-offline",
    ]
    assert public[1].is_hub_online is True
    assert public[2].is_hub_online is True
    assert public[3].is_hub_online is False
    assert hub.checked == ["hub-1", "hub-2"]


def test_matching_weights_and_thresholds_use_module_constants(monkeypatch):
    from agent import matching

    monkeypatch.setattr(matching, "VECTOR_WEIGHT", 0.25)
    monkeypatch.setattr(matching, "CAPABILITY_WEIGHT", 0.75)

    assert matching.compute_final_score(0.2, 1.0) == pytest.approx(0.8)

    monkeypatch.setattr(matching, "GAP_THRESHOLD", 0.5)
    monkeypatch.setattr(matching, "QUALITY_THRESHOLD", 0.5)
    ranked = [
        {"agent_id": "a1", "final_score": 0.8},
        {"agent_id": "a2", "final_score": 0.6},
        {"agent_id": "a3", "final_score": 0.31},
    ]

    assert [m["agent_id"] for m in matching.select_top_matches(ranked)] == [
        "a1",
        "a2",
    ]

    monkeypatch.setattr(matching, "DEBATE_THRESHOLD", 0.3)

    assert [
        m["agent_id"]
        for m in matching.select_top_matches(ranked, is_debate_mode=True)
    ] == ["a1", "a2", "a3"]


@pytest.mark.asyncio
async def test_facade_registry_reads_cards_health_and_ordering():
    facade, repo, _, _, _ = _facade_with_docs([
        {
            "agent_id": "a1",
            "provider_id": "u1",
            "agent_status": "active",
            "capabilities": ["write"],
            "agent_card": {
                "name": "Writer",
                "description": "Writes",
                "url": "https://writer.example",
            },
        },
        {
            "agent_id": "a2",
            "provider_id": "u1",
            "agent_status": "inactive",
            "agent_card": {"name": "Reader", "url": "https://reader.example"},
        },
    ])

    agent = await facade.get_agent("a1")
    missing = await facade.get_agent("missing")
    card = await facade.get_agent_card("a1")
    ordered = await facade.get_agents_by_ids(["a2", "a1", "missing"])

    assert agent.name == "Writer"
    assert agent.status == "active"
    assert missing is None
    assert card.url == "https://writer.example"
    assert card.raw_card["description"] == "Writes"
    assert [item.agent_id for item in ordered] == ["a2", "a1"]
    assert await facade.is_agent_healthy("a1") is True
    assert await facade.is_agent_healthy("a2") is False
    assert await facade.is_agent_healthy("missing") is False
    assert repo.get_by_ids_calls == [["a2", "a1", "missing"]]


@pytest.mark.asyncio
async def test_facade_direct_callability_fails_closed_for_inactive_and_offline_hubs():
    facade, _, _, _, hub = _facade_with_docs(
        [
            {"agent_id": "cloud", "agent_status": "active", "agent_card": {"url": "https://c"}},
            {"agent_id": "inactive", "agent_status": "inactive", "agent_card": {"url": "https://i"}},
            {
                "agent_id": "hub-online",
                "agent_status": "active",
                "source": "hub",
                "hub_id": "hub-1",
                "agent_card": {"url": "https://h"},
            },
            {
                "agent_id": "hub-offline",
                "agent_status": "active",
                "source": "hub",
                "hub_id": "hub-2",
                "agent_card": {"url": "https://h2"},
            },
        ],
        hub_online={"hub-1": True, "hub-2": False},
    )
    no_hub_facade, _, _, _, _ = _facade_with_docs(
        [
            {
                "agent_id": "hub",
                "agent_status": "active",
                "source": "hub",
                "hub_id": "hub-1",
                "agent_card": {"url": "https://h"},
            }
        ],
        hub_online=None,
    )

    assert await facade.is_directly_callable("cloud") is True
    assert await facade.is_directly_callable("inactive") is False
    assert await facade.is_directly_callable("missing") is False
    assert await facade.is_directly_callable("hub-online") is True
    assert await facade.is_directly_callable("hub-offline") is False
    assert await no_hub_facade.is_directly_callable("hub") is False
    assert hub.checked == ["hub-1", "hub-2"]


@pytest.mark.asyncio
async def test_facade_uses_async_hub_liveness_reader():
    from agent import AgentFacade

    repo = FakeRepository(
        [
            {
                "agent_id": "hub",
                "agent_status": "active",
                "source": "hub",
                "hub_id": "hub-1",
                "agent_card": {
                    "name": "Hub Agent",
                    "description": "Desc",
                    "url": "https://hub-agent.example",
                },
            }
        ]
    )
    hub = FakeHubLiveness({"hub-1": True})
    facade = AgentFacade(
        repository=repo,
        vector=FakeVector(),
        llm_provider=FakeLLM(),
        card_resolver=FakeCardResolver(),
        agent_index="a2a-agents",
        hub_liveness=hub,
        id_factory=lambda: "new-agent",
        now=lambda: datetime(2026, 5, 10, tzinfo=UTC),
    )

    info = await facade.get_agent("hub")

    assert info.is_hub_online is True
    assert await facade.is_directly_callable("hub") is True
    assert hub.checked == ["hub-1", "hub-1"]


def test_facade_rejects_sync_hub_liveness_reader_at_bind_time():
    facade, _, _, _, _ = _facade_with_docs([])

    with pytest.raises(TypeError, match="is_hub_online must be async"):
        facade.bind_hub_liveness(SyncFakeHubLiveness())


class FakeRepository:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = {doc["agent_id"]: _copy_doc(doc) for doc in docs}
        self.get_by_ids_calls: list[list[str]] = []
        self.find_normalized_calls: list[tuple[str, str | None]] = []
        self.list_visible_calls: list[dict] = []
        self.prune_calls: list[tuple[str, list[str]]] = []
        self.activate_calls: list[list[str]] = []
        self.offline_calls: list[str] = []
        self.deleted: list[str] = []

    async def get_by_id(self, agent_id: str) -> dict | None:
        doc = self.docs.get(agent_id)
        return _copy_doc(doc) if doc is not None else None

    async def get_by_ids(self, agent_ids: list[str]) -> list[dict]:
        self.get_by_ids_calls.append(list(agent_ids))
        return [_copy_doc(doc) for doc in reversed(self.docs.values()) if doc["agent_id"] in agent_ids]

    async def find_by_normalized_url(
        self, normalized_url: str, provider_id: str | None = None
    ) -> dict | None:
        self.find_normalized_calls.append((normalized_url, provider_id))
        for doc in self.docs.values():
            if doc.get("normalized_url") == normalized_url and (
                provider_id is None or doc.get("provider_id") == provider_id
            ):
                return _copy_doc(doc)
        return None

    async def public_url_exists(self, subdomain: str, base_domain: str) -> bool:
        needle = f"://{subdomain}.{base_domain}"
        return any(needle in (doc.get("public_url") or "") for doc in self.docs.values())

    async def upsert(self, agent_id: str, data: dict) -> None:
        self.docs[agent_id] = {**_copy_doc(data), "agent_id": agent_id}

    async def delete(self, agent_id: str) -> bool:
        self.deleted.append(agent_id)
        return self.docs.pop(agent_id, None) is not None

    async def update(self, agent_id: str, updates: dict) -> dict | None:
        current = self.docs.get(agent_id)
        if current is None:
            return None
        current.update(_copy_doc(updates))
        return _copy_doc(current)

    async def upsert_hub_agent(
        self, hub_id: str, local_agent_id: str, data: dict
    ) -> str:
        for doc in self.docs.values():
            if doc.get("hub_id") == hub_id and doc.get("local_agent_id") == local_agent_id:
                agent_id = doc["agent_id"]
                doc.update(_copy_doc(data))
                doc["agent_id"] = agent_id
                doc["hub_id"] = hub_id
                doc["local_agent_id"] = local_agent_id
                return agent_id
        agent_id = data["agent_id"]
        self.docs[agent_id] = {**_copy_doc(data), "hub_id": hub_id, "local_agent_id": local_agent_id}
        return agent_id

    async def prune_missing_hub_agents(
        self, hub_id: str, active_agent_ids: list[str]
    ) -> int:
        self.prune_calls.append((hub_id, list(active_agent_ids)))
        count = 0
        for doc in self.docs.values():
            if (
                doc.get("hub_id") == hub_id
                and doc["agent_id"] not in active_agent_ids
            ):
                doc["agent_status"] = "inactive"
                if doc.get("source") != "hub":
                    doc.pop("hub_id", None)
                    doc.pop("local_agent_id", None)
                count += 1
        return count

    async def activate_agents(self, agent_ids: list[str]) -> int:
        self.activate_calls.append(list(agent_ids))
        count = 0
        for agent_id in agent_ids:
            if agent_id in self.docs:
                self.docs[agent_id]["agent_status"] = "active"
                count += 1
        return count

    async def get_indexed_description_hash(self, agent_id: str) -> str | None:
        doc = self.docs.get(agent_id)
        if doc is None:
            return None
        return doc.get("indexed_description_hash") or doc.get("description_hash")

    async def set_indexed_description_hash(self, agent_id: str, desc_hash: str) -> None:
        self.docs[agent_id]["indexed_description_hash"] = desc_hash
        self.docs[agent_id]["description_hash"] = desc_hash

    async def mark_hub_agents_offline(self, hub_id: str) -> int:
        self.offline_calls.append(hub_id)
        count = 0
        for doc in self.docs.values():
            if doc.get("hub_id") == hub_id and doc.get("agent_status") == "active":
                doc["agent_status"] = "inactive"
                count += 1
        return count

    async def get_by_provider(self, provider_id: str) -> list[dict]:
        return [_copy_doc(doc) for doc in self.docs.values() if doc.get("provider_id") == provider_id]

    async def get_public(self, limit: int = 50) -> list[dict]:
        docs = [
            _copy_doc(doc)
            for doc in self.docs.values()
            if doc.get("is_public", True) is True
        ]
        return docs[:limit] if limit else docs

    async def list_visible(
        self,
        *,
        user_id: str | None = None,
        active_only: bool = False,
        agent_ids: list[str] | None = None,
        query: dict | None = None,
        limit: int = 0,
    ) -> list[dict]:
        self.list_visible_calls.append(
            {
                "user_id": user_id,
                "active_only": active_only,
                "agent_ids": agent_ids,
                "query": query,
                "limit": limit,
            }
        )
        allowed_ids = set(agent_ids) if agent_ids is not None else None
        visible = []
        for doc in self.docs.values():
            if allowed_ids is not None and doc["agent_id"] not in allowed_ids:
                continue
            if active_only and doc.get("agent_status") != "active":
                continue
            if query and not _matches_doc(doc, query):
                continue
            if doc.get("is_public", True) or (
                user_id is not None and doc.get("provider_id") == user_id
            ):
                visible.append(_copy_doc(doc))
        return visible[:limit] if limit else visible


class FakeVector:
    def __init__(self) -> None:
        self.upserts = []
        self.deletes = []
        self.searches = []
        self.results = []
        self.fail_upsert = False
        self.fail_delete = False

    async def search(
        self,
        index: str,
        vector: list[float],
        top_k: int,
        filter: dict | None = None,
    ) -> list:
        self.searches.append(
            {"index": index, "vector": vector, "top_k": top_k, "filter": filter}
        )
        return list(self.results)

    async def upsert(self, index: str, records: list) -> None:
        if self.fail_upsert:
            raise RuntimeError("vector failed")
        self.upserts.append((index, records))

    async def delete(self, index: str, ids: list[str]) -> None:
        self.deletes.append((index, ids))
        if self.fail_delete:
            raise RuntimeError("vector delete failed")


class FakeLLM:
    def __init__(self) -> None:
        self.embedded: list[str] = []

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        self.embedded.append(text)
        return [0.1, 0.2, 0.3]


class FakeCardResolver:
    def __init__(self, card: AgentCardSnapshot | None = None) -> None:
        self.card = card
        self.urls: list[str] = []

    async def resolve_card(self, agent_url: str) -> AgentCardSnapshot | None:
        self.urls.append(agent_url)
        return self.card


class FakeHubLiveness:
    def __init__(self, online: dict[str, bool]) -> None:
        self._online = online
        self.checked: list[str] = []

    async def is_hub_online(self, hub_id: str) -> bool:
        self.checked.append(hub_id)
        return self._online.get(hub_id, False)

    async def get_hub_owner_id(self, hub_id: str) -> str | None:
        return "user-1"


class SyncFakeHubLiveness:
    def is_hub_online(self, hub_id: str) -> bool:
        return True

    async def get_hub_owner_id(self, hub_id: str) -> str | None:
        return "user-1"


class FakeExclusionReader:
    def __init__(self, excluded: set[str]) -> None:
        self.excluded = frozenset(excluded)
        self.calls = 0

    async def get_excluded_agent_ids(self) -> frozenset[str]:
        self.calls += 1
        return self.excluded


def _facade_with_docs(
    docs: list[dict],
    *,
    hub_online: dict[str, bool] | None = None,
    resolved_card: AgentCardSnapshot | None = None,
    vector: FakeVector | None = None,
    gateway_base_url: str | None = None,
    exclusion_reader: FakeExclusionReader | None = None,
):
    from agent import AgentFacade

    repo = FakeRepository(docs)
    hub_online = hub_online or {}
    hub = FakeHubLiveness(hub_online) if hub_online is not None else None
    vector = vector or FakeVector()
    llm = FakeLLM()
    resolver = FakeCardResolver(resolved_card)
    return (
        AgentFacade(
            repository=repo,
            vector=vector,
            llm_provider=llm,
            card_resolver=resolver,
            agent_index="a2a-agents",
            hub_liveness=hub,
            exclusion_reader=exclusion_reader,
            gateway_base_url=gateway_base_url,
            id_factory=lambda: "new-agent",
            now=lambda: datetime(2026, 5, 10, tzinfo=UTC),
        ),
        repo,
        vector,
        llm,
        hub,
    )


def _matches_doc(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches_doc(doc, branch) for branch in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches_doc(doc, branch) for branch in expected):
                return False
            continue
        if doc.get(key) != expected:
            return False
    return True


def _copy_doc(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    copied = {}
    for key, value in doc.items():
        if isinstance(value, dict):
            copied[key] = _copy_doc(value)
        elif isinstance(value, list):
            copied[key] = list(value)
        else:
            copied[key] = value
    return copied
