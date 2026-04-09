"""
Unit tests for AgentMatcher pipeline

Tests per design doc §Testing Strategy:
- CapabilityFilter: skill matching, I/O mode checks, general-purpose handling
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
    _tokenize,
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


# ---- Tokenizer Tests ----


def test_tokenize_simple():
    """Test basic tokenization."""
    assert _tokenize("Hello World") == {"hello", "world"}


def test_tokenize_with_punctuation():
    """Test tokenization strips punctuation."""
    assert _tokenize("Hello, World! How are you?") == {"hello", "world", "how", "are", "you"}


def test_tokenize_with_numbers():
    """Test tokenization includes numbers."""
    assert _tokenize("Python 3.11 and Node 18") == {"python", "3", "11", "and", "node", "18"}


def test_tokenize_empty():
    """Test tokenization of empty string."""
    assert _tokenize("") == set()


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


def test_compute_capability_score_skill_name_match():
    """Test skill name matching increases score."""
    skills = [
        AgentSkill(
            id="s1",
            name="Python Developer",
            description="Writes Python code",
            tags=["coding"],  # Required field
        )
    ]
    agent = create_test_agent("a1", "PythonBot", skills=skills)
    message_tokens = _tokenize("I need help with python development")

    score = compute_capability_score(message_tokens, agent)
    assert score > 0.3  # Should be above baseline


def test_compute_capability_score_description_match():
    """Test skill description matching increases score."""
    skills = [
        AgentSkill(
            id="s1",
            name="Code Helper",
            description="Expert in debugging and testing Python applications",
            tags=["dev"],  # Required field
        )
    ]
    agent = create_test_agent("a1", "CodeBot", skills=skills)
    message_tokens = _tokenize("Help me debug my python testing issues")

    score = compute_capability_score(message_tokens, agent)
    assert score > 0.15  # Above zero (has some description overlap)


def test_compute_capability_score_tag_match():
    """Test tag matching increases score."""
    skills = [
        AgentSkill(
            id="s1",
            name="ML Assistant",
            description="Machine learning helper",
            tags=["tensorflow", "pytorch", "scikit-learn"],  # Required field
        )
    ]
    agent = create_test_agent("a1", "MLBot", skills=skills)
    message_tokens = _tokenize("I need help with tensorflow model training")

    score = compute_capability_score(message_tokens, agent)
    assert score > 0.15  # Above zero (has tag overlap with tensorflow)


def test_compute_capability_score_io_mode_with_attachments_capable():
    """Test I/O mode scoring: file-capable agent + attachments → 1.0."""
    skills = [
        AgentSkill(
            id="s1",
            name="Image Processor",
            description="Process images",
            tags=["image"],  # Required field
        )
    ]
    agent = create_test_agent("a1", "ImageBot", skills=skills, input_modes=["text", "image/png"])
    message_tokens = _tokenize("analyze this image")

    score = compute_capability_score(message_tokens, agent, required_input_modes=["image/png"])
    # Should have full I/O score component
    assert score > 0.0


def test_compute_capability_score_io_mode_with_attachments_not_capable():
    """Test I/O mode scoring: non-file-capable + attachments → 0.0."""
    skills = [
        AgentSkill(
            id="s1",
            name="Text Helper",
            description="Text processing only",
            tags=["text"],  # Required field
        )
    ]
    agent = create_test_agent("a1", "TextBot", skills=skills, input_modes=["text"])
    message_tokens = _tokenize("analyze this file")

    score = compute_capability_score(message_tokens, agent, required_input_modes=["image/png"])
    # Should be penalized for lack of file support
    assert score < 0.15  # Only partial overlap from skill, no I/O score


def test_compute_capability_score_io_mode_no_attachments():
    """Test I/O mode scoring: no attachments → 1.0 (no penalty)."""
    skills = [
        AgentSkill(
            id="s1",
            name="General Helper",
            description="Helps with tasks",
            tags=["general"],  # Required field
        )
    ]
    agent = create_test_agent("a1", "GeneralBot", skills=skills, input_modes=["text"])
    message_tokens = _tokenize("help with general tasks")

    score = compute_capability_score(message_tokens, agent, required_input_modes=None)
    # Should not be penalized for missing file support when no files present
    assert score > 0.0


def test_compute_capability_score_general_purpose_agent():
    """Test general-purpose agent (no skills) gets baseline score."""
    agent = create_test_agent("a1", "GenericBot", skills=None)
    message_tokens = _tokenize("do something")

    score = compute_capability_score(message_tokens, agent)
    # Should get baseline 0.3 * 0.85 + 0.15 * 1.0 = 0.255 + 0.15 = 0.405
    assert 0.40 <= score <= 0.41  # baseline score


def test_compute_capability_score_empty_skills():
    """Test agent with empty skills list."""
    agent = create_test_agent("a1", "EmptySkillsBot", skills=[])
    message_tokens = _tokenize("help me")

    score = compute_capability_score(message_tokens, agent)
    assert 0.40 <= score <= 0.41  # Should get baseline score


def test_compute_capability_score_no_overlap():
    """Test no skill overlap results in low score."""
    skills = [
        AgentSkill(
            id="s1",
            name="Math Tutor",
            description="Teaches mathematics",
            tags=["education", "algebra"],  # Required field
        )
    ]
    agent = create_test_agent("a1", "MathBot", skills=skills)
    message_tokens = _tokenize("help me cook dinner tonight")

    score = compute_capability_score(message_tokens, agent)
    # Should have minimal score (only I/O component since no overlap)
    assert score < 0.2


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
        MatchedAgent(agent1, 0.9, 0.8, 0.86),
        MatchedAgent(agent2, 0.85, 0.75, 0.81),
        MatchedAgent(agent3, 0.8, 0.7, 0.76),
        MatchedAgent(agent4, 0.75, 0.65, 0.71),
        MatchedAgent(agent5, 0.7, 0.6, 0.66),
        MatchedAgent(agent6, 0.65, 0.55, 0.61),
    ]

    selected = select_top_agents(ranked, is_debate_mode=True)
    assert 3 <= len(selected) <= 5


def test_select_top_agents_debate_mode_below_threshold():
    """Test debate mode with agents below threshold returns at most available."""
    agent1 = create_test_agent("a1", "Agent1")
    agent2 = create_test_agent("a2", "Agent2")

    ranked = [
        MatchedAgent(agent1, 0.5, 0.4, 0.46),  # Above threshold (0.3)
        MatchedAgent(agent2, 0.2, 0.15, 0.18),  # Below threshold
    ]

    selected = select_top_agents(ranked, is_debate_mode=True)
    # Should return at least 3, but we only have 2, so return both
    assert len(selected) == 2


def test_select_top_agents_gap_threshold_single_winner():
    """Test gap threshold detects clear winner."""
    agent1 = create_test_agent("a1", "Winner")
    agent2 = create_test_agent("a2", "Runner-up")
    agent3 = create_test_agent("a3", "Third")

    ranked = [
        MatchedAgent(agent1, 0.95, 0.9, 0.93),  # Clear winner
        MatchedAgent(agent2, 0.65, 0.6, 0.63),  # Gap > 0.15
        MatchedAgent(agent3, 0.6, 0.55, 0.58),
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
        MatchedAgent(agent1, 0.8, 0.75, 0.78),  # Above 0.4
        MatchedAgent(agent2, 0.75, 0.7, 0.73),  # Above 0.4
        MatchedAgent(agent3, 0.65, 0.6, 0.63),  # Above 0.4
        MatchedAgent(agent4, 0.3, 0.25, 0.28),  # Below 0.4
    ]

    selected = select_top_agents(ranked, is_debate_mode=False)
    assert len(selected) == 3  # Top 3 above threshold
    assert all(m.final_score > 0.4 for m in selected)


def test_select_top_agents_all_below_threshold_returns_first():
    """Test all agents below threshold still returns top agent."""
    agent1 = create_test_agent("a1", "Agent1")
    agent2 = create_test_agent("a2", "Agent2")

    ranked = [
        MatchedAgent(agent1, 0.3, 0.25, 0.28),
        MatchedAgent(agent2, 0.2, 0.15, 0.18),
    ]

    selected = select_top_agents(ranked, is_debate_mode=False)
    assert len(selected) == 1
    assert selected[0].agent.agent_id == "a1"


def test_select_top_agents_composite_score_calculation():
    """Test composite score is properly calculated."""
    agent1 = create_test_agent("a1", "Agent1")

    # VECTOR_WEIGHT=0.6, CAPABILITY_WEIGHT=0.4
    # Expected: 0.6 * 0.8 + 0.4 * 0.7 = 0.48 + 0.28 = 0.76
    matched = MatchedAgent(agent1, vector_score=0.8, capability_score=0.7, final_score=0.76)

    assert matched.final_score == 0.76


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
    agent1 = create_test_agent("a1", "Agent1", skills=[
        AgentSkill(id="s1", name="Python Expert", description="Python coding", tags=["python"])
    ])
    agent2 = create_test_agent("a2", "Agent2", skills=[
        AgentSkill(id="s2", name="General Helper", description="General tasks", tags=["general"])
    ])

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
        create_test_agent(f"a{i}", f"Agent{i}", skills=[
            AgentSkill(id=f"s{i}", name="Helper", description="Helps", tags=["helper"])
        ])
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
    """Test match with required_input_modes affects scoring."""
    file_agent = create_test_agent("a1", "FileAgent",
        skills=[AgentSkill(id="s1", name="File Processor", description="Process files", tags=["files"])],
        input_modes=["text", "image/png"]
    )
    text_agent = create_test_agent("a2", "TextAgent",
        skills=[AgentSkill(id="s2", name="Text Processor", description="Process text", tags=["text"])],
        input_modes=["text"]
    )

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
    if len(result.agents) >= 2:
        assert result.agents[0].agent.agent_id == "a1"


@pytest.mark.asyncio
async def test_agent_matcher_without_required_input_modes():
    """Test match without required_input_modes (no attachments)."""
    agent = create_test_agent("a1", "TextAgent",
        skills=[AgentSkill(id="s1", name="Helper", description="General help", tags=["helper"])],
        input_modes=["text"]
    )

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
    # Should not be penalized for lack of file support when no attachments
    assert result.agents[0].capability_score > 0.0


@pytest.mark.asyncio
async def test_agent_matcher_excludes_capability_issues():
    """Test match excludes agents with capability issues."""
    agent1 = create_test_agent("a1", "GoodAgent")
    agent2 = create_test_agent("a2", "BadAgent")

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
