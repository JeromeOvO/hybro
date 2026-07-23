"""
Unit tests for AgentMatcher pipeline

Tests:
- I/O mode file support detection
- CapabilityFilter: structural I/O scoring (no token matching)
- ScoreRanker: composite scoring, debate/quality cutoffs
- AgentMatcher.match(): end-to-end pipeline integration
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.types import AgentCapabilities, AgentCard, AgentProvider, AgentSkill

from agent.matcher import (
    AgentMatcher,
    MatchedAgent,
    MatchResult,
    _agent_supports_files,
    compute_capability_score,
    select_top_agents,
)
from agent.matching import rank_agent_docs
from common.dto.agent import AgentInfo
from models.agent import Agent, AgentStatus

# ---- Fixtures ----


def create_test_agent(
    agent_id: str,
    name: str,
    skills: list[AgentSkill] | None = None,
    input_modes: list[str] | None = None,
) -> Agent:
    """Helper to create test agents."""
    return Agent(
        agent_id=agent_id,
        provider_id="test-provider",
        agent_card=AgentCard(
            name=name,
            description=f"Test agent {name}",
            url=f"https://test.com/{agent_id}",
            version="1.0.0",
            provider=AgentProvider(organization="Test Org", url="https://test.com"),
            capabilities=AgentCapabilities(),
            default_input_modes=input_modes or ["text"],
            default_output_modes=["text"],
            skills=skills or [],
        ),
        agent_status=AgentStatus.active,
    )


def _info_from_agent(agent: Agent) -> AgentInfo:
    return AgentInfo(
        agent_id=agent.agent_id,
        name=agent.agent_card.name,
        description=agent.agent_card.description,
        url=agent.agent_card.url,
        provider_id=agent.provider_id,
        status=agent.agent_status.value,
    )


@pytest.fixture
def mock_db_service():
    """Mock database service."""
    mock = MagicMock()
    mock.query_similar_agents_with_scores = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def mock_capability_issue_service():
    """Mock capability issue service."""
    mock = MagicMock()
    mock.get_excluded_agent_ids = AsyncMock(return_value=frozenset())
    return mock


# ---- File Support Tests ----


def test_agent_supports_files_exact_match():
    """Test file support with exact 'file' mode."""
    agent = create_test_agent("a1", "FileAgent", input_modes=["text", "file"])
    assert _agent_supports_files(agent) is True


def test_agent_supports_files_wildcard():
    """Test file support with wildcard '*/*' mode."""
    agent = create_test_agent("a1", "FileAgent", input_modes=["text", "*/*"])
    assert _agent_supports_files(agent) is True


def test_agent_supports_files_image_prefix():
    """Test file support with image/ prefix."""
    agent = create_test_agent("a1", "ImageAgent", input_modes=["text", "image/png"])
    assert _agent_supports_files(agent) is True


def test_agent_supports_files_video_prefix():
    """Test file support with video/ prefix."""
    agent = create_test_agent("a1", "VideoAgent", input_modes=["text", "video/mp4"])
    assert _agent_supports_files(agent) is True


def test_agent_supports_files_audio_prefix():
    """Test file support with audio/ prefix."""
    agent = create_test_agent("a1", "AudioAgent", input_modes=["text", "audio/wav"])
    assert _agent_supports_files(agent) is True


def test_agent_supports_files_pdf():
    """Test file support with PDF mime type."""
    agent = create_test_agent("a1", "PDFAgent", input_modes=["text", "application/pdf"])
    assert _agent_supports_files(agent) is True


def test_agent_supports_files_zip():
    """Test file support with ZIP mime type."""
    agent = create_test_agent("a1", "ZipAgent", input_modes=["text", "application/zip"])
    assert _agent_supports_files(agent) is True


@pytest.mark.parametrize(
    "input_mode",
    ["application/json", "text/csv", "application/*", "application/"],
)
def test_agent_supports_files_generic_mime_modes(input_mode):
    agent = create_test_agent("a1", "GenericFileAgent", input_modes=[input_mode])
    assert _agent_supports_files(agent) is True


def test_agent_no_file_support():
    """Test agent without file support."""
    agent = create_test_agent("a1", "TextAgent", input_modes=["text"])
    assert _agent_supports_files(agent) is False


def test_agent_no_file_support_default():
    """Test agent with default input modes (text only)."""
    agent = create_test_agent("a1", "DefaultAgent", input_modes=None)
    assert _agent_supports_files(agent) is False


# ---- CapabilityFilter Tests ----


def test_capability_score_no_attachments():
    """No attachments → all agents compatible (1.0)."""
    agent = create_test_agent("a1", "AnyAgent", input_modes=["text"])
    assert compute_capability_score(agent, required_input_modes=None) == 1.0


def test_capability_score_attachments_file_capable():
    """Attachments + file-capable agent → 1.0."""
    agent = create_test_agent("a1", "FileAgent", input_modes=["text", "image/png"])
    assert compute_capability_score(agent, required_input_modes=["image/png"]) == 1.0


def test_capability_score_attachments_not_capable():
    """Attachments + text-only agent → 0.0."""
    agent = create_test_agent("a1", "TextAgent", input_modes=["text"])
    assert compute_capability_score(agent, required_input_modes=["image/png"]) == 0.0


def test_capability_score_attachments_wildcard_capable():
    """Attachments + wildcard agent → 1.0."""
    agent = create_test_agent("a1", "WildcardAgent", input_modes=["*/*"])
    assert (
        compute_capability_score(agent, required_input_modes=["application/pdf"]) == 1.0
    )


def test_capability_score_empty_required_modes_requires_file_support():
    text_agent = create_test_agent("a1", "TextAgent", input_modes=["text"])
    file_agent = create_test_agent("a2", "JsonAgent", input_modes=["application/json"])

    assert compute_capability_score(text_agent, required_input_modes=[]) == 0.0
    assert compute_capability_score(file_agent, required_input_modes=[]) == 1.0


def test_capability_score_accepts_generic_mime_agent():
    agent = create_test_agent("a1", "JsonAgent", input_modes=["application/json"])
    assert (
        compute_capability_score(agent, required_input_modes=["application/json"])
        == 1.0
    )


def test_capability_score_rejects_pdf_for_image_only_agent():
    agent = create_test_agent("a1", "ImageOnly", input_modes=["image/*"])
    assert (
        compute_capability_score(agent, required_input_modes=["application/pdf"]) == 0.0
    )


def test_capability_score_accepts_pdf_for_pdf_agent():
    agent = create_test_agent("a1", "PDFAgent", input_modes=["application/pdf"])
    assert (
        compute_capability_score(agent, required_input_modes=["application/pdf"]) == 1.0
    )


def test_rank_agent_docs_filters_incompatible_attachment_agents():
    text_agent = create_test_agent("a1", "TextAgent", input_modes=["text"])
    pdf_agent = create_test_agent("a2", "PDFAgent", input_modes=["application/pdf"])
    docs = [
        {
            "agent_id": text_agent.agent_id,
            "agent_card": text_agent.agent_card.model_dump(mode="json"),
        },
        {
            "agent_id": pdf_agent.agent_id,
            "agent_card": pdf_agent.agent_card.model_dump(mode="json"),
        },
    ]

    ranked = rank_agent_docs(
        docs,
        {text_agent.agent_id: 1.0, pdf_agent.agent_id: 0.75},
        required_input_modes=["application/pdf"],
    )

    assert [match["agent_id"] for match in ranked] == [pdf_agent.agent_id]
    assert ranked[0]["capability_score"] == 1.0


def test_rank_agent_docs_returns_empty_when_no_agent_accepts_attachment():
    text_agent = create_test_agent("a1", "TextAgent", input_modes=["text"])

    ranked = rank_agent_docs(
        [
            {
                "agent_id": text_agent.agent_id,
                "agent_card": text_agent.agent_card.model_dump(mode="json"),
            }
        ],
        {text_agent.agent_id: 1.0},
        required_input_modes=["application/pdf"],
    )

    assert ranked == []


# ---- ScoreRanker Tests ----


def test_select_top_agents_empty_list():
    """Test empty list returns empty."""
    assert select_top_agents([], is_debate_mode=False) == []
    assert select_top_agents([], is_debate_mode=True) == []


def test_select_top_agents_debate_mode_returns_multiple():
    """Test debate mode returns 3-5 agents."""
    agent1 = create_test_agent("a1", "Agent1")
    agent2 = create_test_agent("a2", "Agent2")
    agent3 = create_test_agent("a3", "Agent3")
    agent4 = create_test_agent("a4", "Agent4")
    agent5 = create_test_agent("a5", "Agent5")
    agent6 = create_test_agent("a6", "Agent6")

    ranked = [
        MatchedAgent(agent1, 0.9, 1.0, 0.92),
        MatchedAgent(agent2, 0.85, 1.0, 0.87),
        MatchedAgent(agent3, 0.8, 1.0, 0.83),
        MatchedAgent(agent4, 0.75, 1.0, 0.79),
        MatchedAgent(agent5, 0.7, 1.0, 0.76),
        MatchedAgent(agent6, 0.65, 1.0, 0.70),
    ]

    selected = select_top_agents(ranked, is_debate_mode=True)
    assert 3 <= len(selected) <= 5


def test_select_top_agents_debate_mode_few_above_threshold():
    """Test debate mode with few agents above threshold returns only those."""
    agent1 = create_test_agent("a1", "Agent1")
    agent2 = create_test_agent("a2", "Agent2")
    agent3 = create_test_agent("a3", "Agent3")

    ranked = [
        MatchedAgent(agent1, 0.5, 1.0, 0.58),  # Above threshold (0.3)
        MatchedAgent(agent2, 0.3, 1.0, 0.41),  # Above threshold
        MatchedAgent(agent3, 0.1, 1.0, 0.24),  # Below threshold
    ]

    selected = select_top_agents(ranked, is_debate_mode=True)
    assert len(selected) == 2  # Only 2 above threshold


def test_select_top_agents_debate_mode_none_above_threshold():
    """Test debate mode with no agents above threshold returns top 2 for meaningful debate."""
    agent1 = create_test_agent("a1", "Agent1")
    agent2 = create_test_agent("a2", "Agent2")

    ranked = [
        MatchedAgent(agent1, 0.2, 1.0, 0.28),  # Below threshold
        MatchedAgent(agent2, 0.1, 1.0, 0.24),  # Below threshold
    ]

    selected = select_top_agents(ranked, is_debate_mode=True)
    assert len(selected) == 2  # Min 2 for meaningful debate
    assert selected[0].agent.agent_id == "a1"
    assert selected[1].agent.agent_id == "a2"


def test_select_top_agents_debate_mode_single_candidate():
    """Test debate mode with only 1 candidate returns that 1."""
    agent1 = create_test_agent("a1", "Agent1")

    ranked = [
        MatchedAgent(agent1, 0.2, 1.0, 0.28),
    ]

    selected = select_top_agents(ranked, is_debate_mode=True)
    assert len(selected) == 1
    assert selected[0].agent.agent_id == "a1"


def test_select_top_agents_debate_mode_one_above_threshold():
    """Test debate mode with only 1 above threshold still returns 2."""
    agent1 = create_test_agent("a1", "Agent1")
    agent2 = create_test_agent("a2", "Agent2")

    ranked = [
        MatchedAgent(agent1, 0.4, 1.0, 0.49),  # Above 0.3
        MatchedAgent(agent2, 0.2, 1.0, 0.28),  # Below 0.3
    ]

    selected = select_top_agents(ranked, is_debate_mode=True)
    assert len(selected) == 2  # Min 2 for meaningful debate


def test_select_top_agents_gap_threshold_single_winner():
    """Test gap threshold detects clear winner."""
    agent1 = create_test_agent("a1", "Winner")
    agent2 = create_test_agent("a2", "Runner-up")
    agent3 = create_test_agent("a3", "Third")

    ranked = [
        MatchedAgent(agent1, 0.95, 1.0, 0.96),  # Clear winner
        MatchedAgent(agent2, 0.65, 1.0, 0.70),  # Gap > 0.15
        MatchedAgent(agent3, 0.6, 1.0, 0.66),
    ]

    selected = select_top_agents(ranked, is_debate_mode=False)
    assert len(selected) == 1
    assert selected[0].agent.agent_id == "a1"


def test_select_top_agents_quality_threshold_cutoff():
    """Test quality threshold filters agents."""
    agent1 = create_test_agent("a1", "Agent1")
    agent2 = create_test_agent("a2", "Agent2")
    agent3 = create_test_agent("a3", "Agent3")
    agent4 = create_test_agent("a4", "Agent4")

    ranked = [
        MatchedAgent(agent1, 0.8, 1.0, 0.83),  # Above 0.4
        MatchedAgent(agent2, 0.75, 1.0, 0.79),  # Above 0.4
        MatchedAgent(agent3, 0.65, 1.0, 0.70),  # Above 0.4
        MatchedAgent(agent4, 0.3, 1.0, 0.41),  # Above 0.4 but capped at 3
    ]

    selected = select_top_agents(ranked, is_debate_mode=False)
    assert len(selected) == 3  # Top 3 above threshold


def test_select_top_agents_all_below_threshold_returns_first():
    """Test all agents below threshold still returns top agent."""
    agent1 = create_test_agent("a1", "Agent1")
    agent2 = create_test_agent("a2", "Agent2")

    ranked = [
        MatchedAgent(agent1, 0.3, 1.0, 0.38),
        MatchedAgent(agent2, 0.2, 1.0, 0.32),
    ]

    selected = select_top_agents(ranked, is_debate_mode=False)
    assert len(selected) == 1
    assert selected[0].agent.agent_id == "a1"


# ---- AgentMatcher.match() Tests ----


@pytest.mark.asyncio
async def test_agent_matcher_no_candidates():
    """Test match with no facade candidates returns empty result."""
    facade = MagicMock()
    facade.match_for_message = AsyncMock(return_value=[])
    matcher = AgentMatcher(facade=facade)

    result = await matcher.match("test message")

    assert isinstance(result, MatchResult)
    assert result.agents == []
    assert result.total_candidates == 0
    assert result.filtered_count == 0


@pytest.mark.asyncio
async def test_agent_matcher_returns_sorted_result():
    """Test adapter converts facade results to MatchResult."""
    agent1 = create_test_agent("a1", "Agent1")
    agent2 = create_test_agent("a2", "Agent2")
    facade = MagicMock()
    facade.match_for_message = AsyncMock(
        return_value=[
            {
                "agent": _info_from_agent(agent1),
                "vector_score": 0.9,
                "capability_score": 1.0,
                "final_score": 0.92,
            },
            {
                "agent": _info_from_agent(agent2),
                "vector_score": 0.7,
                "capability_score": 1.0,
                "final_score": 0.74,
            },
        ]
    )
    matcher = AgentMatcher(facade=facade)

    result = await matcher.match("help with python coding")

    assert isinstance(result, MatchResult)
    assert len(result.agents) > 0
    assert result.total_candidates == 2
    assert result.filtered_count == 2
    assert result.agents[0].final_score >= result.agents[1].final_score


@pytest.mark.asyncio
async def test_agent_matcher_filters_missing_agents_from_stale_matches():
    """Vector hits without live agent records should not reach selection."""
    live_agent = create_test_agent("a1", "LiveAgent")
    facade = MagicMock()
    facade.match_for_message = AsyncMock(
        return_value=[
            {"agent": None, "final_score": 0.99},
            {
                "agent": _info_from_agent(live_agent),
                "vector_score": 0.7,
                "capability_score": 1.0,
                "final_score": 0.74,
            },
            MagicMock(agent=None, score=0.6),
        ]
    )
    matcher = AgentMatcher(facade=facade)

    result = await matcher.match("help with python coding")

    assert [match.agent.agent_id for match in result.agents] == ["a1"]
    assert result.total_candidates == 1
    assert result.filtered_count == 1


@pytest.mark.asyncio
async def test_agent_matcher_debate_mode_returns_more_agents():
    """Test debate mode is forwarded to the facade."""
    agents = [create_test_agent(f"a{i}", f"Agent{i}") for i in range(6)]
    facade = MagicMock()
    facade.match_for_message = AsyncMock(
        return_value=[{"agent": _info_from_agent(agents[0]), "final_score": 0.8}]
    )
    matcher = AgentMatcher(facade=facade)

    # Non-debate mode
    result_normal = await matcher.match("help me", is_debate_mode=False)
    # Debate mode
    result_debate = await matcher.match("help me", is_debate_mode=True)

    assert len(result_debate.agents) == len(result_normal.agents)
    assert facade.match_for_message.call_args_list[-1].kwargs["is_debate_mode"] is True


@pytest.mark.asyncio
async def test_agent_matcher_with_required_input_modes():
    """Test required_input_modes is forwarded."""
    file_agent = create_test_agent("a1", "FileAgent", input_modes=["text", "image/png"])
    facade = MagicMock()
    facade.match_for_message = AsyncMock(
        return_value=[
            {
                "agent": _info_from_agent(file_agent),
                "vector_score": 0.8,
                "capability_score": 1.0,
                "final_score": 0.83,
            }
        ]
    )
    matcher = AgentMatcher(facade=facade)

    result = await matcher.match(
        "process this file", required_input_modes=["image/png"]
    )

    assert len(result.agents) > 0
    assert result.agents[0].agent.agent_id == "a1"
    assert result.agents[0].capability_score == 1.0
    assert facade.match_for_message.call_args.kwargs["required_input_modes"] == [
        "image/png"
    ]


@pytest.mark.asyncio
async def test_agent_matcher_without_required_input_modes():
    """Test match without required_input_modes (no attachments)."""
    agent = create_test_agent("a1", "TextAgent", input_modes=["text"])
    facade = MagicMock()
    facade.match_for_message = AsyncMock(
        return_value=[
            {
                "agent": _info_from_agent(agent),
                "capability_score": 1.0,
                "final_score": 0.83,
            }
        ]
    )
    matcher = AgentMatcher(facade=facade)

    result = await matcher.match("help me with text processing")

    assert len(result.agents) > 0
    # No attachments → capability_score = 1.0
    assert result.agents[0].capability_score == 1.0


@pytest.mark.asyncio
async def test_agent_matcher_excludes_capability_issues():
    """Unbound matcher fails fast instead of falling back to legacy DB logic."""
    matcher = AgentMatcher()
    with pytest.raises(RuntimeError, match="bind_facade"):
        await matcher.match("test message")


@pytest.mark.asyncio
async def test_agent_matcher_with_user_id():
    """Test match passes user_id for private agent visibility."""
    agent = create_test_agent("a1", "PrivateAgent")
    facade = MagicMock()
    facade.match_for_message = AsyncMock(
        return_value=[{"agent": _info_from_agent(agent), "final_score": 0.9}]
    )
    matcher = AgentMatcher(facade=facade)

    await matcher.match("test", user_id="user123")

    assert facade.match_for_message.call_args.kwargs["requesting_user_id"] == "user123"
