"""Tests for AgentSelectionService facade over AgentMatcher."""

from unittest.mock import AsyncMock, patch

import pytest
from a2a.types import AgentCapabilities, AgentCard

from models.agent import Agent, AgentStatus
from models.room import MessageContent, RoomUserMessage, UserAttachment
from services.agent_matcher import MatchedAgent, MatchResult
from services.agent_selection_service import (
    AgentSelectionResult,
    AgentSelectionService,
    RoutingStrategy,
)
from services.room_services import DispatchStrategy, resolve_strategy


def _create_test_agent_card(name: str, description: str) -> AgentCard:
    """Helper to create a minimal AgentCard for testing."""
    return AgentCard(
        name=name,
        description=description,
        url="https://test-agent.example.com/.well-known/agent.json",
        version="1.0.0",
        skills=[],
        capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
    )


@pytest.fixture
def mock_matched_agents():
    """Create mock MatchedAgent instances."""
    agents = []
    for i in range(3):
        agent = Agent(
            agent_id=f"agent-{i}",
            agent_card=_create_test_agent_card(f"Agent {i}", f"Test agent {i}"),
            agent_status=AgentStatus.active,
        )
        matched = MatchedAgent(
            agent=agent,
            vector_score=0.9 - (i * 0.1),
            capability_score=0.8 - (i * 0.1),
            final_score=0.85 - (i * 0.1),
        )
        agents.append(matched)
    return agents


@pytest.mark.asyncio
async def test_facade_delegates_to_matcher(mock_matched_agents):
    """Test that facade correctly delegates to AgentMatcher."""
    mock_match_result = MatchResult(
        agents=mock_matched_agents,
        total_candidates=10,
        filtered_count=5,
    )

    with patch("services.agent_matcher.AgentMatcher") as MockMatcher:
        mock_matcher_instance = AsyncMock()
        mock_matcher_instance.match.return_value = mock_match_result
        MockMatcher.return_value = mock_matcher_instance

        service = AgentSelectionService()
        result = await service.select_agents_for_message(
            message_text="test message",
            user_id="user-123",
        )

        # Verify matcher was called
        mock_matcher_instance.match.assert_called_once_with(
            message_text="test message",
            user_id="user-123",
            is_debate_mode=False,
            required_input_modes=None,
        )

        # Verify result conversion
        assert isinstance(result, AgentSelectionResult)
        assert len(result.agents) == 3
        assert result.agents[0].agent_id == "agent-0"
        assert result.agents[0].agent_name == "Agent 0"
        assert "Match score:" in result.agents[0].reason
        assert result.reasoning == "Matched 3 agent(s) from 10 candidates"


@pytest.mark.asyncio
async def test_facade_backward_compat_single(mock_matched_agents):
    """Test that single agent returns SINGLE strategy."""
    mock_match_result = MatchResult(
        agents=[mock_matched_agents[0]],
        total_candidates=10,
        filtered_count=5,
    )

    with patch("services.agent_matcher.AgentMatcher") as MockMatcher:
        mock_matcher_instance = AsyncMock()
        mock_matcher_instance.match.return_value = mock_match_result
        MockMatcher.return_value = mock_matcher_instance

        service = AgentSelectionService()
        result = await service.select_agents_for_message(
            message_text="test message",
        )

        assert result.strategy == RoutingStrategy.SINGLE
        assert result.needs_debate is False
        assert len(result.agents) == 1


@pytest.mark.asyncio
async def test_facade_backward_compat_parallel(mock_matched_agents):
    """Test that multiple agents returns PARALLEL strategy."""
    mock_match_result = MatchResult(
        agents=mock_matched_agents,
        total_candidates=10,
        filtered_count=5,
    )

    with patch("services.agent_matcher.AgentMatcher") as MockMatcher:
        mock_matcher_instance = AsyncMock()
        mock_matcher_instance.match.return_value = mock_match_result
        MockMatcher.return_value = mock_matcher_instance

        service = AgentSelectionService()
        result = await service.select_agents_for_message(
            message_text="test message",
        )

        assert result.strategy == RoutingStrategy.PARALLEL
        assert result.needs_debate is False
        assert len(result.agents) == 3


@pytest.mark.asyncio
async def test_facade_empty_result():
    """Test that empty result returns appropriate response."""
    mock_match_result = MatchResult(
        agents=[],
        total_candidates=0,
        filtered_count=0,
    )

    with patch("services.agent_matcher.AgentMatcher") as MockMatcher:
        mock_matcher_instance = AsyncMock()
        mock_matcher_instance.match.return_value = mock_match_result
        MockMatcher.return_value = mock_matcher_instance

        service = AgentSelectionService()
        result = await service.select_agents_for_message(
            message_text="test message",
        )

        assert result.strategy == RoutingStrategy.SINGLE
        assert result.needs_debate is False
        assert len(result.agents) == 0
        assert "No matching agents" in result.reasoning


@pytest.mark.asyncio
async def test_facade_passes_required_input_modes(mock_matched_agents):
    """Test that required_input_modes is correctly forwarded."""
    mock_match_result = MatchResult(
        agents=mock_matched_agents,
        total_candidates=10,
        filtered_count=5,
    )

    with patch("services.agent_matcher.AgentMatcher") as MockMatcher:
        mock_matcher_instance = AsyncMock()
        mock_matcher_instance.match.return_value = mock_match_result
        MockMatcher.return_value = mock_matcher_instance

        service = AgentSelectionService()
        await service.select_agents_for_message(
            message_text="test message",
            required_input_modes=["image/png", "image/jpeg"],
        )

        # Verify required_input_modes was passed through
        mock_matcher_instance.match.assert_called_once()
        call_kwargs = mock_matcher_instance.match.call_args[1]
        assert call_kwargs["required_input_modes"] == ["image/png", "image/jpeg"]


@pytest.mark.asyncio
async def test_facade_passes_is_debate_mode(mock_matched_agents):
    """Test that is_debate_mode is correctly forwarded."""
    mock_match_result = MatchResult(
        agents=mock_matched_agents,
        total_candidates=10,
        filtered_count=5,
    )

    with patch("services.agent_matcher.AgentMatcher") as MockMatcher:
        mock_matcher_instance = AsyncMock()
        mock_matcher_instance.match.return_value = mock_match_result
        MockMatcher.return_value = mock_matcher_instance

        service = AgentSelectionService()
        await service.select_agents_for_message(
            message_text="test message",
            is_debate_mode=True,
        )

        # Verify is_debate_mode was passed through
        mock_matcher_instance.match.assert_called_once()
        call_kwargs = mock_matcher_instance.match.call_args[1]
        assert call_kwargs["is_debate_mode"] is True


def test_derive_required_input_modes_with_attachments():
    """Test _derive_required_input_modes with attachments."""
    from services.room_services import RoomServices

    user_message = RoomUserMessage(
        room_id="room-123",
        message_id="msg-123",
        user_id="user-123",
        message_content=MessageContent(
            message_text="test",
            attachments=[
                UserAttachment(
                    file_id="file-1",
                    s3_key="key-1",
                    mime_type="image/png",
                    file_name="test.png",
                    size_bytes=1024,
                ),
                UserAttachment(
                    file_id="file-2",
                    s3_key="key-2",
                    mime_type="application/pdf",
                    file_name="doc.pdf",
                    size_bytes=2048,
                ),
            ],
        ),
    )

    result = RoomServices._derive_required_input_modes(user_message)
    assert result is not None
    assert result == ["image/png", "application/pdf"]


def test_derive_required_input_modes_no_attachments():
    """Test _derive_required_input_modes without attachments."""
    from services.room_services import RoomServices

    user_message = RoomUserMessage(
        room_id="room-123",
        message_id="msg-123",
        user_id="user-123",
        message_content=MessageContent(
            message_text="test",
            attachments=None,
        ),
    )

    result = RoomServices._derive_required_input_modes(user_message)
    assert result is None


def test_resolve_strategy_supervisor():
    """Test resolve_strategy with supervisor mode."""
    strategy = resolve_strategy(
        use_supervisor=True,
        is_debate_mode=False,
        agent_count=3,
    )
    assert strategy == DispatchStrategy.SUPERVISOR


def test_resolve_strategy_debate():
    """Test resolve_strategy with debate mode."""
    strategy = resolve_strategy(
        use_supervisor=False,
        is_debate_mode=True,
        agent_count=3,
    )
    assert strategy == DispatchStrategy.SEQUENTIAL_DEBATE


def test_resolve_strategy_multi_agent():
    """Test resolve_strategy with multiple agents."""
    strategy = resolve_strategy(
        use_supervisor=False,
        is_debate_mode=False,
        agent_count=3,
    )
    assert strategy == DispatchStrategy.SEQUENTIAL


def test_resolve_strategy_single():
    """Test resolve_strategy with single agent."""
    strategy = resolve_strategy(
        use_supervisor=False,
        is_debate_mode=False,
        agent_count=1,
    )
    assert strategy == DispatchStrategy.SINGLE


@pytest.mark.asyncio
async def test_facade_propagates_matcher_error():
    """Test that matcher exceptions propagate to callers for proper error handling."""
    with patch("services.agent_matcher.AgentMatcher") as MockMatcher:
        mock_matcher_instance = AsyncMock()
        mock_matcher_instance.match.side_effect = RuntimeError("Pinecone unreachable")
        MockMatcher.return_value = mock_matcher_instance

        service = AgentSelectionService()
        with pytest.raises(RuntimeError, match="Pinecone unreachable"):
            await service.select_agents_for_message(
                message_text="test message",
            )


@pytest.mark.asyncio
async def test_facade_respects_top_k(mock_matched_agents):
    """Test that top_k caps the number of returned agents."""
    mock_match_result = MatchResult(
        agents=mock_matched_agents,  # 3 agents
        total_candidates=10,
        filtered_count=5,
    )

    with patch("services.agent_matcher.AgentMatcher") as MockMatcher:
        mock_matcher_instance = AsyncMock()
        mock_matcher_instance.match.return_value = mock_match_result
        MockMatcher.return_value = mock_matcher_instance

        service = AgentSelectionService()
        result = await service.select_agents_for_message(
            message_text="test message",
            top_k=1,
        )

        assert len(result.agents) == 1
        assert result.agents[0].agent_id == "agent-0"
        assert result.strategy == RoutingStrategy.SINGLE


def test_resolve_strategy_supervisor_overrides_debate():
    """Supervisor takes precedence over debate mode."""
    strategy = resolve_strategy(
        use_supervisor=True,
        is_debate_mode=True,
        agent_count=3,
    )
    assert strategy == DispatchStrategy.SUPERVISOR


def test_resolve_strategy_debate_with_single_agent():
    """Debate mode applies even with 1 agent."""
    strategy = resolve_strategy(
        use_supervisor=False,
        is_debate_mode=True,
        agent_count=1,
    )
    assert strategy == DispatchStrategy.SEQUENTIAL_DEBATE


@pytest.mark.asyncio
async def test_suggest_agents_uses_facade(mock_matched_agents):
    """Test that suggest_agents still works through facade."""
    mock_match_result = MatchResult(
        agents=mock_matched_agents[:2],  # Return 2 agents
        total_candidates=10,
        filtered_count=5,
    )

    with patch("services.agent_matcher.AgentMatcher") as MockMatcher:
        mock_matcher_instance = AsyncMock()
        mock_matcher_instance.match.return_value = mock_match_result
        MockMatcher.return_value = mock_matcher_instance

        service = AgentSelectionService()
        result = await service.suggest_agents(
            message_text="test message",
            top_k=3,
        )

        # Verify result structure
        assert "routing_strategy" in result
        assert "reasoning" in result
        assert "needs_debate" in result
        assert "suggested_agents" in result

        assert result["routing_strategy"] == "parallel"  # 2 agents
        assert result["needs_debate"] is False
        assert len(result["suggested_agents"]) == 2
        assert result["suggested_agents"][0]["agent_id"] == "agent-0"
        assert result["suggested_agents"][0]["name"] == "Agent 0"
        assert "reason" in result["suggested_agents"][0]
