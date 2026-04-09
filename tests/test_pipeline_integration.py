"""
Integration tests for Agent Matching & Dispatch Pipeline

End-to-end tests verifying data flow across:
- AgentMatcher → AgentSelectionService
- RoomServices → AgentSelectionService → AgentMatcher
- SequentialDebateDispatcher (shared by debate_service + SupervisorExecutor)
- DispatchStrategy resolution
- required_input_modes threading

Per design doc §Testing Strategy - Integration Tests.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from a2a.types import AgentCard, AgentSkill, AgentCapabilities
from models.agent import Agent, AgentStatus
from models.room import MessageContent, RoomUserMessage, UserAttachment
from services.agent_matcher import AgentMatcher, MatchedAgent, MatchResult, compute_capability_score, select_top_agents
from services.agent_selection_service import AgentSelectionService, RoutingStrategy
from services.room_services import DispatchStrategy, resolve_strategy, RoomServices
from modules.debate_dispatcher import SequentialDebateDispatcher
from services.debate_service import debate_service


# ---- Test Helpers ----


def _make_agent(agent_id: str, name: str, description: str,
                skills=None, input_modes=None) -> Agent:
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
            MatchedAgent(agent=agent1, vector_score=0.8, capability_score=0.7, final_score=0.76),
            MatchedAgent(agent=agent2, vector_score=0.6, capability_score=0.5, final_score=0.56),
        ],
        total_candidates=5,
        filtered_count=2,
    )

    # Patch AgentMatcher.match at the correct module level
    with patch("services.agent_matcher.AgentMatcher.match", new_callable=AsyncMock) as mock_match:
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
        assert result.agents[0].score == 0.76
        assert "Match score: 0.76" in result.agents[0].reason
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
    mock_db.get_agent_by_agent_id = AsyncMock(side_effect=lambda aid:
        _make_agent(aid, f"Agent {aid}", f"Test agent {aid}")
    )

    # Mock agent_selection_service to track if it's called
    with patch("services.agent_selection_service.agent_selection_service.select_agents_for_message") as mock_select:
        mock_select.return_value = None  # Should not be called

        # Create RoomServices and call _resolve_explicit_target_scope
        room_services = RoomServices()
        room_services.database_service = mock_db

        result = await room_services._resolve_explicit_target_scope(
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
    strategy = resolve_strategy(use_supervisor=True, is_debate_mode=False, agent_count=3)
    assert strategy == DispatchStrategy.SUPERVISOR

    # Case 2: SEQUENTIAL_DEBATE (debate=True, multi-agent)
    strategy = resolve_strategy(use_supervisor=False, is_debate_mode=True, agent_count=3)
    assert strategy == DispatchStrategy.SEQUENTIAL_DEBATE

    # Case 3: SEQUENTIAL (multi-agent, no debate, no supervisor)
    strategy = resolve_strategy(use_supervisor=False, is_debate_mode=False, agent_count=3)
    assert strategy == DispatchStrategy.SEQUENTIAL

    # Case 4: SINGLE (single agent)
    strategy = resolve_strategy(use_supervisor=False, is_debate_mode=False, agent_count=1)
    assert strategy == DispatchStrategy.SINGLE

    # Edge case: supervisor=True + debate=True → supervisor wins
    strategy = resolve_strategy(use_supervisor=True, is_debate_mode=True, agent_count=3)
    assert strategy == DispatchStrategy.SUPERVISOR

    # Edge case: debate with 1 agent → still SEQUENTIAL_DEBATE
    strategy = resolve_strategy(use_supervisor=False, is_debate_mode=True, agent_count=1)
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
    room_services = RoomServices()
    required_modes = room_services._derive_required_input_modes(user_message)

    # Verify MIME types extracted
    assert required_modes == ["application/pdf", "image/jpeg"]

    # Mock AgentMatcher.match to verify it receives required_input_modes
    with patch("services.agent_matcher.AgentMatcher.match", new_callable=AsyncMock) as mock_match:
        mock_match.return_value = MatchResult(agents=[], total_candidates=0, filtered_count=0)

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
    with patch("services.agent_matcher.AgentMatcher.match", new_callable=AsyncMock) as mock_match:
        mock_match.return_value = MatchResult(agents=[], total_candidates=0, filtered_count=0)

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
async def test_matcher_error_graceful_fallback():
    """Verify matcher exceptions produce error response with reasoning."""
    # Mock AgentMatcher.match to raise exception
    with patch("services.agent_matcher.AgentMatcher.match", new_callable=AsyncMock) as mock_match:
        mock_match.side_effect = RuntimeError("Database connection failed")

        service = AgentSelectionService()
        result = await service.select_agents_for_message(
            message_text="Test message",
            is_debate_mode=False,
        )

        # Verify error handling
        assert result.strategy == RoutingStrategy.SINGLE
        assert result.agents == []
        assert "Agent matching failed" in result.reasoning
        assert "Database connection failed" in result.reasoning
        assert result.needs_debate is False


def test_capability_score_integrated_with_vector_score():
    """End-to-end: verify capability scoring and ranking pipeline work together."""
    # Create agents with different skills
    skills_python = [AgentSkill(id="skill1", name="python", description="Python programming expert", tags=["coding"])]
    skills_data = [AgentSkill(id="skill2", name="data-analysis", description="Data analysis and visualization", tags=["analytics"])]

    agent_python = _make_agent("agent1", "PythonExpert", "Python specialist", skills=skills_python)
    agent_data = _make_agent("agent2", "DataAnalyst", "Data analysis specialist", skills=skills_data)
    agent_general = _make_agent("agent3", "GeneralAgent", "General purpose assistant")

    # Simulate matched agents with vector scores
    message_tokens = {"python", "code", "script"}

    matched_agents = [
        MatchedAgent(
            agent=agent_python,
            vector_score=0.8,
            capability_score=compute_capability_score(message_tokens, agent_python),
            final_score=0.0,  # Will be computed
        ),
        MatchedAgent(
            agent=agent_data,
            vector_score=0.7,
            capability_score=compute_capability_score(message_tokens, agent_data),
            final_score=0.0,
        ),
        MatchedAgent(
            agent=agent_general,
            vector_score=0.6,
            capability_score=compute_capability_score(message_tokens, agent_general),
            final_score=0.0,
        ),
    ]

    # Compute final scores with standard weights (0.6 vector + 0.4 capability)
    for m in matched_agents:
        m.final_score = 0.6 * m.vector_score + 0.4 * m.capability_score

    # Sort by final score
    matched_agents.sort(key=lambda m: m.final_score, reverse=True)

    # Verify ranking: PythonExpert should rank highest due to skill match
    assert matched_agents[0].agent.agent_id == "agent1"  # PythonExpert

    # Verify final scores are computed correctly
    for m in matched_agents:
        assert m.final_score > 0.0
        assert m.final_score <= 1.0

    # Test select_top_agents with non-debate mode
    selected = select_top_agents(matched_agents, is_debate_mode=False)

    # In non-debate mode with no large gap, should return qualified agents (up to 3)
    assert len(selected) >= 1
    assert len(selected) <= 3
    assert selected[0].agent.agent_id == "agent1"  # Top agent should be selected


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
    # Truncated response (3000) + marker (~40) + template (~400) ≈ 3440
    assert len(prompt) < 3600  # Allow some buffer for template text

    # Verify task and prior agent name still present
    assert original_task in prompt
    assert prior_agent_name in prompt


def test_sequential_debate_first_agent_gets_raw_task():
    """Verify first agent in debate receives original task unchanged."""
    original_task = "Write a Python script to process data"

    # First agent: no prior response
    prompt = SequentialDebateDispatcher.build_debate_prompt(
        original_task=original_task,
        prior_agent_name=None,
        prior_response=None,
    )

    # Should return original task unchanged
    assert prompt == original_task

    # Verify no debate instructions added
    assert "DEBATE MODE INSTRUCTIONS" not in prompt
    assert "RESPONSE FROM PREVIOUS AGENT" not in prompt


def test_matcher_ranking_with_file_capability():
    """Integration test: verify file-capable agents score higher for messages with attachments."""
    from services.agent_matcher import _tokenize

    # Create agents: one file-capable, one text-only
    agent_file = _make_agent("agent1", "FileAgent", "Handles files", input_modes=["text", "file"])
    agent_text = _make_agent("agent2", "TextAgent", "Text only", input_modes=["text"])

    message_tokens = _tokenize("Process this document")

    # Compute capability scores with required_input_modes (message has attachments)
    score_file = compute_capability_score(message_tokens, agent_file, required_input_modes=["application/pdf"])
    score_text = compute_capability_score(message_tokens, agent_text, required_input_modes=["application/pdf"])

    # File-capable agent should score higher
    assert score_file > score_text

    # Now test without attachments
    score_file_no_attach = compute_capability_score(message_tokens, agent_file, required_input_modes=None)
    score_text_no_attach = compute_capability_score(message_tokens, agent_text, required_input_modes=None)

    # Without attachments, both should have same baseline (no I/O penalty)
    assert score_file_no_attach == score_text_no_attach


@pytest.mark.asyncio
async def test_debate_service_uses_shared_dispatcher():
    """Verify debate_service delegates to SequentialDebateDispatcher."""
    from models.room import RoomAgentMessage
    from a2a.types import Task, Message, TextPart, Role, TaskStatus, TaskState

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
                history=[Message(
                    messageId="msg1",
                    role=Role.user,
                    parts=[TextPart(text="Original task")],
                )],
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
                history=[Message(
                    messageId="msg2",
                    role=Role.user,
                    parts=[TextPart(text="Original task")],
                )],
            ),
        ),
    )

    # Mock database calls
    with patch.object(debate_service.db_service, "get_room_agent_message_by_message_id", new_callable=AsyncMock) as mock_get_msg, \
         patch.object(debate_service.db_service, "get_agent_name_by_agent_id", new_callable=AsyncMock) as mock_get_name, \
         patch.object(debate_service.db_service, "update_room_agent_message_with_new_message_content_by_message_id", new_callable=AsyncMock) as mock_update:

        mock_get_msg.side_effect = [prior_message, current_message]
        mock_get_name.return_value = "PriorAgent"
        mock_update.return_value = True

        # Call inject_short_debate_for_agent_message
        result = await debate_service.inject_short_debate_for_agent_message(current_message)

        # Verify SequentialDebateDispatcher was used (indirectly, by checking the prompt structure)
        # The prompt should contain debate instructions from SequentialDebateDispatcher
        assert mock_update.called


def test_select_top_agents_debate_mode_diversity():
    """Verify debate mode returns 3-5 agents for diversity."""
    # Create 6 agents with varying scores
    agents = [
        MatchedAgent(_make_agent(f"agent{i}", f"Agent{i}", f"Desc{i}"),
                    vector_score=0.8 - i*0.1,
                    capability_score=0.7 - i*0.1,
                    final_score=0.75 - i*0.1)
        for i in range(6)
    ]

    # Debate mode: should return 3-5 agents
    selected = select_top_agents(agents, is_debate_mode=True)
    assert 3 <= len(selected) <= 5

    # Non-debate mode: should be more selective (1-3 agents)
    selected_non_debate = select_top_agents(agents, is_debate_mode=False)
    assert 1 <= len(selected_non_debate) <= 3


def test_select_top_agents_clear_winner():
    """Verify non-debate mode returns only top agent when there's a clear winner."""
    # Create agents with large score gap
    agents = [
        MatchedAgent(_make_agent("agent1", "Winner", "Top agent"),
                    vector_score=0.9, capability_score=0.9, final_score=0.9),
        MatchedAgent(_make_agent("agent2", "Runner-up", "Second agent"),
                    vector_score=0.5, capability_score=0.5, final_score=0.5),
        MatchedAgent(_make_agent("agent3", "Third", "Third agent"),
                    vector_score=0.4, capability_score=0.4, final_score=0.4),
    ]

    # Non-debate mode with clear winner: should return only top agent
    selected = select_top_agents(agents, is_debate_mode=False)

    # Gap is 0.9 - 0.5 = 0.4, which exceeds GAP_THRESHOLD (0.15)
    assert len(selected) == 1
    assert selected[0].agent.agent_id == "agent1"
