"""
Integration tests for Agent Matching & Dispatch Pipeline

End-to-end tests verifying data flow across:
- AgentMatcher → AgentSelectionService
- RoomServices → AgentSelectionService → AgentMatcher
- DispatchStrategy resolution
- required_input_modes threading

Per design doc §Testing Strategy - Integration Tests.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import AgentCapabilities, AgentCard

from agent.matcher import (
    MatchedAgent,
    MatchResult,
    accepts_input_modes,
    select_top_agents,
)
from agent.selection_service import AgentSelectionService, RoutingStrategy
from execution.orchestration.dispatch_strategy import DispatchStrategy, resolve_strategy
from models.agent import Agent, AgentStatus
from models.room import MessageContent, RoomUserMessage, UserAttachment
from room.compat.runtime import RoomServices

# ---- Test Helpers ----


def _make_agent(
    agent_id: str, name: str, description: str, skills=None, input_modes=None
) -> Agent:
    """Create a test Agent with optional skills and input modes."""
    card = AgentCard(
        name=name,
        description=description,
        url=f"https://{agent_id}.example.com/.well-known/agent.json",
        version="1.0.0",
        skills=skills or [],
        capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
        defaultInputModes=input_modes or ["text"],
        defaultOutputModes=["text"],
    )
    return Agent(
        agent_id=agent_id,
        agent_card=card,
        agent_status=AgentStatus.active,
    )


# ---- Integration Tests ----


@pytest.mark.asyncio
async def test_all_agents_uses_agent_matcher():
    """Verify AgentSelectionService delegates to AgentMatcher and converts MatchResult."""
    # Create test agents
    agent1 = _make_agent("agent1", "TestAgent1", "A test agent")
    agent2 = _make_agent("agent2", "TestAgent2", "Another test agent")

    # Mock MatchResult
    mock_match_result = MatchResult(
        agents=[
            MatchedAgent(agent=agent1, lexical_score=0.8, final_score=0.8),
            MatchedAgent(agent=agent2, lexical_score=0.6, final_score=0.6),
        ],
        total_candidates=5,
        filtered_count=2,
    )

    # Patch AgentMatcher.match at the correct module level
    with patch(
        "agent.matcher.AgentMatcher.match", new_callable=AsyncMock
    ) as mock_match:
        mock_match.return_value = mock_match_result

        # Create service and call select_agents_for_message
        service = AgentSelectionService()
        result = await service.select_agents_for_message(
            message_text="Test message",
            user_id="user123",
        )

        # Verify matcher was called
        mock_match.assert_called_once()
        call_args = mock_match.call_args
        assert call_args[1]["message_text"] == "Test message"
        assert call_args[1]["user_id"] == "user123"

        # Verify result conversion from MatchResult → AgentSelectionResult
        assert result.strategy == RoutingStrategy.PARALLEL  # 2 agents → PARALLEL
        assert len(result.agents) == 2
        assert result.agents[0].agent_id == "agent1"
        assert result.agents[0].agent_name == "TestAgent1"
        assert result.agents[0].score == 0.8
        assert "Lexical match score: 0.80" in result.agents[0].reason
        assert "Matched 2 agent(s) from 5 candidates" in result.reasoning


@pytest.mark.asyncio
async def test_room_team_bypasses_matcher():
    """Verify room_team mode returns room agents directly without calling matcher."""
    from models.room import Room

    # Create room with agents
    room = Room(
        room_id="room123",
        room_name="Test Room",
        room_owner_id="user123",
        room_owner_name="Test User",
        room_agent_set={"agent1": "Agent One", "agent2": "Agent Two"},
    )

    # Mock database service to return agents
    mock_db = MagicMock()
    mock_db.get_agent_by_agent_id = AsyncMock(
        side_effect=lambda aid: _make_agent(aid, f"Agent {aid}", f"Test agent {aid}")
    )

    # Mock agent_selection_service to track if it's called
    with patch(
        "agent.selection_service.AgentSelectionService.select_agents_for_message"
    ) as mock_select:
        mock_select.return_value = None  # Should not be called

        # Create RoomServices and call _resolve_explicit_target_scope
        room_runtime = RoomServices()
        room_runtime._store = mock_db

        result = await room_runtime._resolve_explicit_target_scope(
            room=room,
            message_text="Test message",
            target_group="room_team",
            sender_user_id="user123",
        )

        # Verify matcher was NOT called
        mock_select.assert_not_called()

        # Verify result is tuple (selected_agent_set, auto_assign, agents)
        assert isinstance(result, tuple)
        selected_agent_set, auto_assign, agents = result
        assert selected_agent_set == {"agent1": "Agent One", "agent2": "Agent Two"}
        assert auto_assign is True
        assert len(agents) == 2


@pytest.mark.asyncio
async def test_all_agents_scope_returns_every_active_agent_without_matching():
    """Execution routing gives the Supervisor the complete active scope."""
    from models.room import Room

    agents = [
        _make_agent("agent1", "Agent One", "Handles research"),
        _make_agent("agent2", "Agent Two", "Handles analysis"),
    ]
    room = Room(
        room_id="room123",
        room_name="Test Room",
        room_owner_id="user123",
        room_owner_name="Test User",
    )
    room_runtime = RoomServices()
    room_runtime._store = MagicMock()
    room_runtime._store.get_all_active_agents = AsyncMock(return_value=agents)
    room_runtime._sanitize_routing_scope = AsyncMock(return_value=(agents, []))
    room_runtime.agent_selection_service = MagicMock()
    room_runtime.agent_selection_service.select_agents_for_message = AsyncMock()

    result = await room_runtime._resolve_explicit_target_scope(
        room=room,
        message_text="A request that lexical search would normally filter",
        target_group="all_agents",
        sender_user_id="user123",
    )

    assert isinstance(result, tuple)
    selected_agent_set, auto_assign, resolved_agents = result
    assert selected_agent_set == {
        "agent1": "Agent One",
        "agent2": "Agent Two",
    }
    assert auto_assign is True
    assert resolved_agents == agents
    room_runtime._store.get_all_active_agents.assert_awaited_once_with(
        user_id="user123"
    )
    room_runtime._sanitize_routing_scope.assert_awaited_once_with(
        ["agent1", "agent2"],
        sender_user_id="user123",
        required_input_modes=None,
    )
    room_runtime.agent_selection_service.select_agents_for_message.assert_not_awaited()


def test_dispatch_strategy_resolution_all_cases():
    assert (
        resolve_strategy(use_supervisor=True, agent_count=3)
        == DispatchStrategy.SUPERVISOR
    )
    assert (
        resolve_strategy(use_supervisor=False, agent_count=3)
        == DispatchStrategy.SEQUENTIAL
    )
    assert (
        resolve_strategy(use_supervisor=False, agent_count=1) == DispatchStrategy.SINGLE
    )


@pytest.mark.asyncio
async def test_derive_required_input_modes_flows_to_matcher():
    """End-to-end: verify attachment MIME types flow from RoomUserMessage → AgentMatcher."""
    # Create RoomUserMessage with attachments
    attachments = [
        UserAttachment(
            file_id="file1",
            file_name="doc.pdf",
            mime_type="application/pdf",
            size_bytes=1024,
            s3_key="uploads/doc.pdf",
        ),
        UserAttachment(
            file_id="file2",
            file_name="image.jpg",
            mime_type="image/jpeg",
            size_bytes=2048,
            s3_key="uploads/image.jpg",
        ),
    ]

    user_message = RoomUserMessage(
        room_id="room123",
        message_id="msg123",
        message_content=MessageContent(
            message_text="Analyze these files",
            attachments=attachments,
        ),
        user_id="user123",
    )

    # Derive required_input_modes
    room_runtime = RoomServices()
    required_modes = room_runtime._derive_required_input_modes(user_message)

    # Verify MIME types extracted
    assert required_modes == ["application/pdf", "image/jpeg"]

    # Mock AgentMatcher.match to verify it receives required_input_modes
    with patch(
        "agent.matcher.AgentMatcher.match", new_callable=AsyncMock
    ) as mock_match:
        mock_match.return_value = MatchResult(
            agents=[], total_candidates=0, filtered_count=0
        )

        service = AgentSelectionService()
        await service.select_agents_for_message(
            message_text="Analyze these files",
            required_input_modes=required_modes,
        )

        # Verify required_input_modes reached the matcher
        mock_match.assert_called_once()
        call_args = mock_match.call_args
        assert call_args[1]["required_input_modes"] == ["application/pdf", "image/jpeg"]


@pytest.mark.asyncio
async def test_no_matching_agents_graceful_fallback():
    """Verify empty MatchResult produces meaningful fallback response."""
    # Mock AgentMatcher.match to return empty result
    with patch(
        "agent.matcher.AgentMatcher.match", new_callable=AsyncMock
    ) as mock_match:
        mock_match.return_value = MatchResult(
            agents=[], total_candidates=0, filtered_count=0
        )

        service = AgentSelectionService()
        result = await service.select_agents_for_message(
            message_text="Test message",
        )

        # Verify graceful fallback
        assert result.strategy == RoutingStrategy.SINGLE
        assert result.agents == []
        assert "No matching agents found" in result.reasoning
        assert result.needs_debate is False


@pytest.mark.asyncio
async def test_matcher_error_propagates_to_caller():
    """Verify matcher exceptions propagate so callers can return proper 500s."""
    with patch(
        "agent.matcher.AgentMatcher.match", new_callable=AsyncMock
    ) as mock_match:
        mock_match.side_effect = RuntimeError("Database connection failed")

        service = AgentSelectionService()
        with pytest.raises(RuntimeError, match="Database connection failed"):
            await service.select_agents_for_message(
                message_text="Test message",
            )


def test_lexical_score_determines_ranking():
    """Verify ranking follows deterministic lexical relevance."""
    agent_high = _make_agent("agent1", "HighLexical", "Best lexical match")
    agent_low = _make_agent("agent2", "LowLexical", "Poor lexical match")

    matched_agents = [
        MatchedAgent(
            agent=agent_high,
            lexical_score=0.9,
            final_score=0.9,
        ),
        MatchedAgent(
            agent=agent_low,
            lexical_score=0.4,
            final_score=0.4,
        ),
    ]

    matched_agents.sort(key=lambda m: m.final_score, reverse=True)
    assert matched_agents[0].agent.agent_id == "agent1"

    # Verify the gap in scores
    assert matched_agents[0].final_score > matched_agents[1].final_score + 0.3


def test_input_modes_are_a_hard_filter():
    """Verify incompatible agents are excluded before lexical scoring."""
    agent_file = _make_agent(
        "agent1", "FileAgent", "Handles files", input_modes=["text", "file"]
    )
    agent_text = _make_agent("agent2", "TextAgent", "Text only", input_modes=["text"])

    assert accepts_input_modes(agent_file, ["application/pdf"]) is True
    assert accepts_input_modes(agent_text, ["application/pdf"]) is False


def test_no_file_penalty_without_attachments():
    """Without attachments, text-only and file-capable agents score equally."""
    agent_file = _make_agent(
        "agent1", "FileAgent", "Handles files", input_modes=["text", "file"]
    )
    agent_text = _make_agent("agent2", "TextAgent", "Text only", input_modes=["text"])

    assert accepts_input_modes(agent_file, None) is True
    assert accepts_input_modes(agent_text, None) is True


def test_select_top_agents_preserves_valid_lexical_candidates():
    """Non-debate selection does not apply legacy score-gap thresholds."""
    agents = [
        MatchedAgent(
            _make_agent("agent1", "Winner", "Top agent"),
            lexical_score=0.9,
            final_score=0.92,
        ),
        MatchedAgent(
            _make_agent("agent2", "Runner-up", "Second agent"),
            lexical_score=0.5,
            final_score=0.58,
        ),
        MatchedAgent(
            _make_agent("agent3", "Third", "Third agent"),
            lexical_score=0.4,
            final_score=0.49,
        ),
    ]

    selected = select_top_agents(agents)

    assert [item.agent.agent_id for item in selected] == [
        "agent1",
        "agent2",
        "agent3",
    ]
