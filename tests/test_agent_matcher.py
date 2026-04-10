"""
Unit tests for AgentMatcher pipeline

Tests:
- I/O mode file support detection
- CapabilityFilter: structural I/O scoring (no token matching)
- ScoreRanker: composite scoring, debate/quality cutoffs
- AgentMatcher.match(): end-to-end pipeline integration
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from a2a.types import AgentCard, AgentCapabilities, AgentProvider, AgentSkill
from models.agent import Agent, AgentStatus
from services.agent_matcher import (
    AgentMatcher,
    MatchedAgent,
    MatchResult,
    _agent_supports_files,
    compute_capability_score,
    select_top_agents,
)


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
    assert compute_capability_score(agent, required_input_modes=["application/pdf"]) == 1.0


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
    """Test debate mode with no agents above threshold returns top 1 fallback."""
    agent1 = create_test_agent("a1", "Agent1")
    agent2 = create_test_agent("a2", "Agent2")

    ranked = [
        MatchedAgent(agent1, 0.2, 1.0, 0.28),  # Below threshold
        MatchedAgent(agent2, 0.1, 1.0, 0.24),  # Below threshold
    ]

    selected = select_top_agents(ranked, is_debate_mode=True)
    assert len(selected) == 1
    assert selected[0].agent.agent_id == "a1"


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
    """Test match with no candidates returns empty result."""
    mock_db = MagicMock()
    mock_db.query_similar_agents_with_scores = AsyncMock(return_value=[])

    mock_capability = MagicMock()
    mock_capability.get_excluded_agent_ids = AsyncMock(return_value=frozenset())

    matcher = AgentMatcher(database_service=mock_db)
    matcher._capability_issue_service = mock_capability

    result = await matcher.match("test message")

    assert isinstance(result, MatchResult)
    assert result.agents == []
    assert result.total_candidates == 0
    assert result.filtered_count == 0


@pytest.mark.asyncio
async def test_agent_matcher_returns_sorted_result():
    """Test match returns sorted MatchResult."""
    agent1 = create_test_agent("a1", "Agent1")
    agent2 = create_test_agent("a2", "Agent2")

    mock_db = MagicMock()
    mock_db.query_similar_agents_with_scores = AsyncMock(return_value=[
        (agent1, 0.9),
        (agent2, 0.7),
    ])

    mock_capability = MagicMock()
    mock_capability.get_excluded_agent_ids = AsyncMock(return_value=frozenset())

    matcher = AgentMatcher(database_service=mock_db)
    matcher._capability_issue_service = mock_capability

    result = await matcher.match("help with python coding")

    assert isinstance(result, MatchResult)
    assert len(result.agents) > 0
    assert result.total_candidates == 2
    assert result.filtered_count == 2
    # Verify sorted descending by final_score
    for i in range(len(result.agents) - 1):
        assert result.agents[i].final_score >= result.agents[i + 1].final_score


@pytest.mark.asyncio
async def test_agent_matcher_debate_mode_returns_more_agents():
    """Test debate mode returns more agents than non-debate."""
    agents = [
        create_test_agent(f"a{i}", f"Agent{i}")
        for i in range(6)
    ]

    mock_db = MagicMock()
    mock_db.query_similar_agents_with_scores = AsyncMock(return_value=[
        (agent, 0.8 - i * 0.05) for i, agent in enumerate(agents)
    ])

    mock_capability = MagicMock()
    mock_capability.get_excluded_agent_ids = AsyncMock(return_value=frozenset())

    matcher = AgentMatcher(database_service=mock_db)
    matcher._capability_issue_service = mock_capability

    # Non-debate mode
    result_normal = await matcher.match("help me", is_debate_mode=False)
    # Debate mode
    result_debate = await matcher.match("help me", is_debate_mode=True)

    # Debate should return more agents (3-5 vs up to 3)
    assert len(result_debate.agents) >= len(result_normal.agents)


@pytest.mark.asyncio
async def test_agent_matcher_with_required_input_modes():
    """Test match with required_input_modes penalizes non-file agents."""
    file_agent = create_test_agent("a1", "FileAgent", input_modes=["text", "image/png"])
    text_agent = create_test_agent("a2", "TextAgent", input_modes=["text"])

    mock_db = MagicMock()
    mock_db.query_similar_agents_with_scores = AsyncMock(return_value=[
        (file_agent, 0.8),
        (text_agent, 0.8),  # Same vector score
    ])

    mock_capability = MagicMock()
    mock_capability.get_excluded_agent_ids = AsyncMock(return_value=frozenset())

    matcher = AgentMatcher(database_service=mock_db)
    matcher._capability_issue_service = mock_capability

    result = await matcher.match("process this file", required_input_modes=["image/png"])

    assert len(result.agents) > 0
    # File-capable agent should be ranked higher due to I/O scoring
    assert result.agents[0].agent.agent_id == "a1"
    assert result.agents[0].capability_score == 1.0


@pytest.mark.asyncio
async def test_agent_matcher_without_required_input_modes():
    """Test match without required_input_modes (no attachments)."""
    agent = create_test_agent("a1", "TextAgent", input_modes=["text"])

    mock_db = MagicMock()
    mock_db.query_similar_agents_with_scores = AsyncMock(return_value=[
        (agent, 0.8),
    ])

    mock_capability = MagicMock()
    mock_capability.get_excluded_agent_ids = AsyncMock(return_value=frozenset())

    matcher = AgentMatcher(database_service=mock_db)
    matcher._capability_issue_service = mock_capability

    result = await matcher.match("help me with text processing")

    assert len(result.agents) > 0
    # No attachments → capability_score = 1.0
    assert result.agents[0].capability_score == 1.0


@pytest.mark.asyncio
async def test_agent_matcher_excludes_capability_issues():
    """Test match excludes agents with capability issues."""
    agent1 = create_test_agent("a1", "GoodAgent")

    mock_db = MagicMock()
    mock_db.query_similar_agents_with_scores = AsyncMock(return_value=[
        (agent1, 0.9),
    ])

    mock_capability = MagicMock()
    mock_capability.get_excluded_agent_ids = AsyncMock(return_value=frozenset({"a2"}))

    matcher = AgentMatcher(database_service=mock_db)
    matcher._capability_issue_service = mock_capability

    result = await matcher.match("test message")

    # Verify excluded set was passed to DB query
    mock_db.query_similar_agents_with_scores.assert_called_once()
    call_kwargs = mock_db.query_similar_agents_with_scores.call_args[1]
    assert "a2" in call_kwargs["excluded_agent_ids"]


@pytest.mark.asyncio
async def test_agent_matcher_with_user_id():
    """Test match passes user_id for private agent visibility."""
    agent = create_test_agent("a1", "PrivateAgent")

    mock_db = MagicMock()
    mock_db.query_similar_agents_with_scores = AsyncMock(return_value=[
        (agent, 0.9),
    ])

    mock_capability = MagicMock()
    mock_capability.get_excluded_agent_ids = AsyncMock(return_value=frozenset())

    matcher = AgentMatcher(database_service=mock_db)
    matcher._capability_issue_service = mock_capability

    result = await matcher.match("test", user_id="user123")

    # Verify user_id was passed to DB query
    mock_db.query_similar_agents_with_scores.assert_called_once()
    call_kwargs = mock_db.query_similar_agents_with_scores.call_args[1]
    assert call_kwargs["user_id"] == "user123"
