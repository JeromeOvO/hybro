"""Tests for AgentSelectionService facade over AgentMatcher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import AgentCapabilities, AgentCard

from agent.matcher import MatchedAgent, MatchResult
from agent.protocols import AgentSuggestion
from agent.selection_service import (
    AgentSelectionResult,
    AgentSelectionService,
    RoutingStrategy,
)
from models.agent import Agent, AgentStatus
from models.room import MessageContent, RoomUserMessage, UserAttachment


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
            lexical_score=0.9 - (i * 0.1),
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

    with patch("agent.selection_service.AgentMatcher") as MockMatcher:
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
            required_input_modes=None,
        )

        # Verify result conversion
        assert isinstance(result, AgentSelectionResult)
        assert len(result.agents) == 3
        assert result.agents[0].agent_id == "agent-0"
        assert result.agents[0].agent_name == "Agent 0"
        assert "Lexical match score:" in result.agents[0].reason
        assert result.reasoning == "Matched 3 agent(s) from 10 candidates"


@pytest.mark.asyncio
async def test_facade_backward_compat_single(mock_matched_agents):
    """Test that single agent returns SINGLE strategy."""
    mock_match_result = MatchResult(
        agents=[mock_matched_agents[0]],
        total_candidates=10,
        filtered_count=5,
    )

    with patch("agent.selection_service.AgentMatcher") as MockMatcher:
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

    with patch("agent.selection_service.AgentMatcher") as MockMatcher:
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

    with patch("agent.selection_service.AgentMatcher") as MockMatcher:
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
async def test_llm_reranks_only_head_and_preserves_lexical_tail():
    matched_agents = []
    for index in range(7):
        agent = Agent(
            agent_id=f"agent-{index}",
            agent_card=_create_test_agent_card(f"Agent {index}", "Test"),
            agent_status=AgentStatus.active,
        )
        matched_agents.append(
            MatchedAgent(
                agent=agent,
                lexical_score=1 - index / 10,
                final_score=1 - index / 10,
            )
        )
    matcher = AsyncMock()
    matcher.match.return_value = MatchResult(
        agents=matched_agents,
        total_candidates=7,
        filtered_count=7,
    )
    reranker = AsyncMock()
    reranker.rank_agents_for_task.return_value = [
        "agent-4",
        "agent-0",
        "agent-1",
        "agent-2",
        "agent-3",
    ]

    result = await AgentSelectionService(
        matcher=matcher,
        llm_reranker=reranker,
    ).select_agents_for_message("test", top_k=7)

    assert [agent.agent_id for agent in result.agents] == [
        "agent-4",
        "agent-0",
        "agent-1",
        "agent-2",
        "agent-3",
        "agent-5",
        "agent-6",
    ]


@pytest.mark.asyncio
async def test_facade_passes_required_input_modes(mock_matched_agents):
    """Test that required_input_modes is correctly forwarded."""
    mock_match_result = MatchResult(
        agents=mock_matched_agents,
        total_candidates=10,
        filtered_count=5,
    )

    with patch("agent.selection_service.AgentMatcher") as MockMatcher:
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


def test_derive_required_input_modes_with_attachments():
    """Test _derive_required_input_modes with attachments."""
    from room.compat.runtime import RoomServices

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
    from room.compat.runtime import RoomServices

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


@pytest.mark.asyncio
async def test_facade_propagates_matcher_error():
    """Test that matcher exceptions propagate to callers for proper error handling."""
    with patch("agent.selection_service.AgentMatcher") as MockMatcher:
        mock_matcher_instance = AsyncMock()
        mock_matcher_instance.match.side_effect = RuntimeError("Mongo unavailable")
        MockMatcher.return_value = mock_matcher_instance

        service = AgentSelectionService()
        with pytest.raises(RuntimeError, match="Mongo unavailable"):
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

    with patch("agent.selection_service.AgentMatcher") as MockMatcher:
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


@pytest.mark.asyncio
async def test_llm_rerank_sanitizes_unknown_duplicate_and_missing_ids(
    mock_matched_agents,
):
    matcher = MagicMock()
    matcher.match = AsyncMock(
        return_value=MatchResult(
            agents=mock_matched_agents,
            total_candidates=3,
            filtered_count=3,
        )
    )
    reranker = MagicMock()
    reranker.rank_agents_for_task = AsyncMock(
        return_value=["unknown", "agent-2", "agent-2"]
    )

    result = await AgentSelectionService(
        matcher=matcher,
        llm_reranker=reranker,
    ).select_agents_for_message("test")

    assert [item.agent_id for item in result.agents] == [
        "agent-2",
        "agent-0",
        "agent-1",
    ]


@pytest.mark.asyncio
async def test_llm_rerank_failure_preserves_lexical_order(mock_matched_agents):
    matcher = MagicMock()
    matcher.match = AsyncMock(
        return_value=MatchResult(
            agents=mock_matched_agents,
            total_candidates=3,
            filtered_count=3,
        )
    )
    reranker = MagicMock()
    reranker.rank_agents_for_task = AsyncMock(side_effect=TimeoutError)

    result = await AgentSelectionService(
        matcher=matcher,
        llm_reranker=reranker,
    ).select_agents_for_message("test")

    assert [item.agent_id for item in result.agents] == [
        "agent-0",
        "agent-1",
        "agent-2",
    ]


async def test_suggest_agents_uses_facade(mock_matched_agents):
    """Test that suggest_agents still works through facade."""
    mock_match_result = MatchResult(
        agents=mock_matched_agents[:2],  # Return 2 agents
        total_candidates=10,
        filtered_count=5,
    )

    with patch("agent.selection_service.AgentMatcher") as MockMatcher:
        mock_matcher_instance = AsyncMock()
        mock_matcher_instance.match.return_value = mock_match_result
        MockMatcher.return_value = mock_matcher_instance

        mock_reranker = AsyncMock()
        service = AgentSelectionService(llm_reranker=mock_reranker)
        result = await service.suggest_agents(
            message_text="test message",
            top_k=3,
            user_id="owner-user",
        )

        mock_matcher_instance.match.assert_awaited_once_with(
            message_text="test message",
            user_id="owner-user",
            required_input_modes=None,
        )
        mock_reranker.rank_agents_for_task.assert_not_awaited()
        assert result.metadata["routing_strategy"] == "parallel"  # 2 agents
        assert result.metadata["reasoning"] == result.analysis
        assert result.metadata["needs_debate"] is False
        assert len(result.suggested_agents) == 2
        assert isinstance(result.suggested_agents[0], AgentSuggestion)
        assert result.suggested_agents[0].agent_id == "agent-0"
        assert result.suggested_agents[0].name == "Agent 0"
        assert result.suggested_agents[0].reason
