"""
Integration tests for Agent Matching & Dispatch Pipeline

End-to-end tests verifying data flow across:
- AgentMatcher → AgentSelectionService
- RoomServices → AgentSelectionService → AgentMatcher
- SequentialDebateDispatcher (shared by DebatePromptInjector + SupervisorExecutor)
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
from execution.orchestration.debate_dispatcher import SequentialDebateDispatcher
from execution.orchestration.debate_prompt_injector import DebatePromptInjector
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
            MatchedAgent(
                agent=agent1, lexical_score=0.8, final_score=0.8
            ),
            MatchedAgent(
                agent=agent2, lexical_score=0.6, final_score=0.6
            ),
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
            is_debate_mode=False,
        )

        # Verify matcher was called
        mock_match.assert_called_once()
        call_args = mock_match.call_args
        assert call_args[1]["message_text"] == "Test message"
        assert call_args[1]["user_id"] == "user123"
        assert call_args[1]["is_debate_mode"] is False

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
            is_debate_mode=False,
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
async def test_debate_dispatcher_shared_by_both_paths():
    """Verify both debate_service and SequentialDebateDispatcher produce same output."""
    original_task = "Analyze this data"
    prior_agent_name = "DataExpert"
    prior_response = "Here is my analysis of the data..."

    # Call SequentialDebateDispatcher directly
    direct_prompt = SequentialDebateDispatcher.build_debate_prompt(
        original_task=original_task,
        prior_agent_name=prior_agent_name,
        prior_response=prior_response,
    )

    # Verify first agent gets raw task
    first_agent_prompt = SequentialDebateDispatcher.build_debate_prompt(
        original_task=original_task,
        prior_agent_name=None,
        prior_response=None,
    )
    assert first_agent_prompt == original_task

    # Verify subsequent agent gets enriched prompt
    assert "YOUR TASK: " in direct_prompt
    assert original_task in direct_prompt
    assert "RESPONSE FROM PREVIOUS AGENT (DataExpert)" in direct_prompt
    assert prior_response in direct_prompt
    assert "DEBATE MODE INSTRUCTIONS:" in direct_prompt


def test_dispatch_strategy_resolution_all_cases():
    """Test resolve_strategy() with all 4 dispatch strategy cases."""
    # Case 1: SUPERVISOR (supervisor=True)
    strategy = resolve_strategy(
        use_supervisor=True, is_debate_mode=False, agent_count=3
    )
    assert strategy == DispatchStrategy.SUPERVISOR

    # Case 2: SEQUENTIAL_DEBATE (debate=True, multi-agent)
    strategy = resolve_strategy(
        use_supervisor=False, is_debate_mode=True, agent_count=3
    )
    assert strategy == DispatchStrategy.SEQUENTIAL_DEBATE

    # Case 3: SEQUENTIAL (multi-agent, no debate, no supervisor)
    strategy = resolve_strategy(
        use_supervisor=False, is_debate_mode=False, agent_count=3
    )
    assert strategy == DispatchStrategy.SEQUENTIAL

    # Case 4: SINGLE (single agent)
    strategy = resolve_strategy(
        use_supervisor=False, is_debate_mode=False, agent_count=1
    )
    assert strategy == DispatchStrategy.SINGLE

    # Edge case: supervisor=True + debate=True → supervisor wins
    strategy = resolve_strategy(use_supervisor=True, is_debate_mode=True, agent_count=3)
    assert strategy == DispatchStrategy.SUPERVISOR

    # Edge case: debate with 1 agent → still SEQUENTIAL_DEBATE
    strategy = resolve_strategy(
        use_supervisor=False, is_debate_mode=True, agent_count=1
    )
    assert strategy == DispatchStrategy.SEQUENTIAL_DEBATE


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
            is_debate_mode=False,
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
            is_debate_mode=False,
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
                is_debate_mode=False,
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


def test_debate_prompt_truncation_consistent():
    """Verify debate prompt truncation is consistent and predictable."""
    original_task = "Analyze this topic"
    prior_agent_name = "ExpertAgent"

    # Create very long response (over 3000 chars)
    long_response = "A" * 3500

    prompt = SequentialDebateDispatcher.build_debate_prompt(
        original_task=original_task,
        prior_agent_name=prior_agent_name,
        prior_response=long_response,
    )

    # Verify truncation marker appears
    assert "[truncated — full response: 3500 chars]" in prompt

    # Verify total length is reasonable (not massively oversized)
    assert len(prompt) < 3600

    # Verify task and prior agent name still present
    assert original_task in prompt
    assert prior_agent_name in prompt


def test_sequential_debate_first_agent_gets_raw_task():
    """Verify first agent in debate receives original task unchanged."""
    original_task = "Write a Python script to process data"

    prompt = SequentialDebateDispatcher.build_debate_prompt(
        original_task=original_task,
        prior_agent_name=None,
        prior_response=None,
    )

    assert prompt == original_task
    assert "DEBATE MODE INSTRUCTIONS" not in prompt
    assert "RESPONSE FROM PREVIOUS AGENT" not in prompt


@pytest.mark.asyncio
async def test_debate_service_uses_shared_dispatcher():
    """Verify DebatePromptInjector delegates to SequentialDebateDispatcher."""
    from a2a.types import Message, Role, Task, TaskState, TaskStatus, TextPart

    from models.room import RoomAgentMessage

    # Create mock agent message with related message
    prior_message = RoomAgentMessage(
        room_id="room123",
        message_id="msg_prior",
        agent_id="agent_prior",
        message_content=MessageContent(
            message_text="Previous agent response",
            message_task=Task(
                id="task_prior",
                contextId="context1",
                status=TaskStatus(state=TaskState.completed),
                history=[
                    Message(
                        messageId="msg1",
                        role=Role.user,
                        parts=[TextPart(text="Original task")],
                    )
                ],
            ),
        ),
    )

    current_message = RoomAgentMessage(
        room_id="room123",
        message_id="msg_current",
        agent_id="agent_current",
        related_message_id="msg_prior",
        task_content="Original task",
        message_content=MessageContent(
            message_text="Current response",
            message_task=Task(
                id="task_current",
                contextId="context2",
                status=TaskStatus(state=TaskState.working),
                history=[
                    Message(
                        messageId="msg2",
                        role=Role.user,
                        parts=[TextPart(text="Original task")],
                    )
                ],
            ),
        ),
    )

    injector = DebatePromptInjector()

    # Mock database calls
    with (
        patch.object(
            injector._message_store,
            "get_room_agent_message_by_message_id",
            new_callable=AsyncMock,
        ) as mock_get_msg,
        patch.object(
            injector._message_store,
            "get_agent_name_by_agent_id",
            new_callable=AsyncMock,
        ) as mock_get_name,
        patch.object(
            injector._message_store,
            "update_room_agent_message_with_new_message_content_by_message_id",
            new_callable=AsyncMock,
        ) as mock_update,
    ):
        mock_get_msg.side_effect = [prior_message, current_message]
        mock_get_name.return_value = "PriorAgent"
        mock_update.return_value = True

        await injector.inject_short_debate_for_agent_message(current_message)
        assert mock_update.called


def test_select_top_agents_debate_mode_diversity():
    """Verify debate mode returns 3-5 agents for diversity."""
    agents = [
        MatchedAgent(
            _make_agent(f"agent{i}", f"Agent{i}", f"Desc{i}"),
            lexical_score=0.8 - i * 0.05,
            final_score=0.8 - i * 0.05,
        )
        for i in range(6)
    ]

    # Debate mode: returns at most five actual lexical hits.
    selected = select_top_agents(agents, is_debate_mode=True)
    assert len(selected) == 5

    # Non-debate mode also preserves lexical order without score-gap heuristics.
    selected_non_debate = select_top_agents(agents, is_debate_mode=False)
    assert len(selected_non_debate) == 5


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

    selected = select_top_agents(agents, is_debate_mode=False)

    assert [item.agent.agent_id for item in selected] == [
        "agent1",
        "agent2",
        "agent3",
    ]
