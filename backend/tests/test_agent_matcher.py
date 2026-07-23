import pytest
from a2a.types import AgentCapabilities, AgentCard, AgentProvider

from agent.matcher import AgentMatcher
from agent.matching import (
    FALLBACK_HIT_THRESHOLD,
    accepts_input_modes,
    is_searchable_query,
    lexical_fallback_score,
    rank_agent_docs,
)
from common.dto.agent import AgentInfo
from models.agent import Agent, AgentStatus


def _doc(
    agent_id: str,
    *,
    name: str,
    description: str = "",
    skills: list[dict] | None = None,
    input_modes: list[str] | None = None,
) -> dict:
    return {
        "agent_id": agent_id,
        "agent_status": "active",
        "agent_card": {
            "name": name,
            "description": description,
            "skills": skills or [],
            "defaultInputModes": input_modes or ["text"],
            "url": f"https://example.com/{agent_id}",
        },
    }


def _agent(agent_id: str, name: str) -> Agent:
    return Agent(
        agent_id=agent_id,
        provider_id="owner",
        agent_status=AgentStatus.active,
        agent_card=AgentCard(
            name=name,
            description=f"{name} description",
            url=f"https://example.com/{agent_id}",
            version="1",
            provider=AgentProvider(organization="Test", url="https://example.com"),
            capabilities=AgentCapabilities(),
            default_input_modes=["text"],
            default_output_modes=["text"],
            skills=[],
        ),
    )


def test_lexical_fallback_normalizes_unicode_and_scores_weighted_fields():
    agent = _doc(
        "a1",
        name="Ｆｉｎａｎｃｅ Planner",
        skills=[{"name": "Budgeting", "tags": ["cash-flow"]}],
    )
    assert lexical_fallback_score("finance", agent) == 1.0
    assert lexical_fallback_score("budgeting", agent) == pytest.approx(0.9)
    assert lexical_fallback_score("cash flow", agent) >= FALLBACK_HIT_THRESHOLD


def test_lexical_fallback_does_not_score_top_level_agent_tags():
    agent = _doc("a1", name="Unrelated")
    agent["agent_card"]["tags"] = ["finance"]

    assert lexical_fallback_score("finance", agent) == 0.0


def test_lexical_fallback_supports_cjk_bigrams_and_single_char_queries():
    agent = _doc("a1", name="旅行规划", description="酒店和交通建议")
    assert lexical_fallback_score("旅行", agent) == 1.0
    assert lexical_fallback_score("酒店", agent) == pytest.approx(0.7)
    assert lexical_fallback_score("旅", agent) == 1.0


def test_empty_and_punctuation_queries_are_not_searchable():
    assert is_searchable_query("   ") is False
    assert is_searchable_query("?!…") is False
    assert is_searchable_query("数据") is True


def test_unicode_latin_queries_are_searchable_and_tokenized_as_words():
    agent = _doc("a1", name="Café Øresund")
    assert is_searchable_query("é") is True
    assert is_searchable_query("ø") is True
    assert lexical_fallback_score("café", agent) == 1.0
    assert lexical_fallback_score("øresund", agent) == 1.0


def test_rank_combines_mongo_and_fallback_and_breaks_ties_by_agent_id():
    docs = [
        _doc("b", name="Writer"),
        _doc("a", name="Writer"),
        _doc("c", name="Unrelated"),
    ]
    ranked = rank_agent_docs(
        docs,
        {"b": 2.0, "a": 2.0},
        mongo_matched_ids={"a", "b"},
        query="writer",
    )
    assert [item["agent_id"] for item in ranked] == ["a", "b"]
    assert all(item["lexical_score"] == 1.0 for item in ranked)


def test_input_modes_are_a_hard_filter():
    image_agent = _doc("a", name="Vision", input_modes=["image/png"])
    text_agent = _doc("b", name="Writer", input_modes=["text"])
    assert accepts_input_modes(image_agent, ["image/png"]) is True
    assert accepts_input_modes(text_agent, ["image/png"]) is False


@pytest.mark.asyncio
async def test_agent_matcher_converts_lexical_scores_without_vector_fields():
    first = _agent("a1", "Writer")

    class Facade:
        async def match_for_message(self, *_args, **_kwargs):
            return [
                {
                    "agent": AgentInfo(
                        agent_id=first.agent_id,
                        name=first.agent_card.name,
                        description=first.agent_card.description,
                        url=first.agent_card.url,
                        raw_card=first.agent_card.model_dump(mode="json"),
                    ),
                    "lexical_score": 0.8,
                    "final_score": 0.8,
                }
            ]

    facade = Facade()
    result = await AgentMatcher(facade=facade).match("write a report")
    assert result.agents[0].lexical_score == 0.8
    assert not hasattr(result.agents[0], "vector_score")
