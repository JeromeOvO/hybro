"""
Unit tests for Phase 5: Supervisor V2 Integration.

Tests cover:
1. build_supervisor_context() wiring into _prepare_for_supervisor_v2()
2. build_agent_execution_context() wiring into process_agent_message()
3. add_synthesis_to_history() in RoomMemoryService
4. update_room_summary() with LLM extraction
5. Compaction trigger in _handle_v2_run_result() for terminal statuses
6. Prompt cache optimization (conversation_context in system prompt)

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §11, §12.3, §18 Phase 5 for specification.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.memory import (
    ConversationTurn,
    MemoryContent,
    RoomMemory,
    RoomSummary,
    TurnRole,
    TurnRepresentation,
)


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def room_memory():
    """Create a minimal RoomMemory for testing."""
    turns = [
        ConversationTurn(
            role=TurnRole.USER,
            content="Hello, I need help with testing.",
            timestamp=datetime(2026, 2, 20, 10, 0),
        ),
        ConversationTurn(
            role=TurnRole.AGENT,
            content="Sure, I can help with that!",
            agent_name="TestAgent",
            timestamp=datetime(2026, 2, 20, 10, 1),
        ),
    ]
    return RoomMemory(
        room_id="test_room",
        memory_content=MemoryContent(conversation_history=turns),
        room_summary=RoomSummary(current_goal="Write tests"),
    )


@pytest.fixture
def mock_db_service():
    """Mock DatabaseService."""
    db = AsyncMock()
    return db


@pytest.fixture
def mock_openai_service():
    """Mock OpenAIService."""
    svc = AsyncMock()
    return svc


# =========================================================================
# Test: add_synthesis_to_history
# =========================================================================


class TestAddSynthesisToHistory:
    """Tests for RoomMemoryService.add_synthesis_to_history()."""

    @pytest.mark.asyncio
    async def test_adds_supervisor_turn_and_persists(
        self, room_memory, mock_db_service, mock_openai_service
    ):
        """Synthesis text should be added as a SUPERVISOR turn and saved to DB."""
        mock_db_service.get_room_memory_by_room_id.return_value = room_memory
        mock_db_service.update_room_memory_by_room_id.return_value = True

        from services.memory_service import RoomMemoryService

        service = RoomMemoryService()
        service.database_service = mock_db_service
        service.openai_service = mock_openai_service

        result = await service.add_synthesis_to_history(
            room_id="test_room",
            synthesis_text="Combined results: Agent A found X, Agent B found Y.",
        )

        assert result is not None  # Returns turn_id on success
        mock_db_service.update_room_memory_by_room_id.assert_awaited_once()

        saved_memory = mock_db_service.update_room_memory_by_room_id.call_args[0][1]
        last_turn = saved_memory.memory_content.conversation_history[-1]
        assert last_turn.role == TurnRole.SUPERVISOR
        assert "Combined results" in last_turn.content

    @pytest.mark.asyncio
    async def test_returns_none_when_room_not_found(
        self, mock_db_service, mock_openai_service
    ):
        """Should return None when room memory doesn't exist."""
        mock_db_service.get_room_memory_by_room_id.return_value = None

        from services.memory_service import RoomMemoryService

        service = RoomMemoryService()
        service.database_service = mock_db_service
        service.openai_service = mock_openai_service

        result = await service.add_synthesis_to_history(
            room_id="nonexistent",
            synthesis_text="Some synthesis",
        )

        assert result is None
        mock_db_service.update_room_memory_by_room_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_none_when_db_update_fails(
        self, room_memory, mock_db_service, mock_openai_service
    ):
        """Should return None when DB persistence fails."""
        mock_db_service.get_room_memory_by_room_id.return_value = room_memory
        mock_db_service.update_room_memory_by_room_id.return_value = False

        from services.memory_service import RoomMemoryService

        service = RoomMemoryService()
        service.database_service = mock_db_service
        service.openai_service = mock_openai_service

        result = await service.add_synthesis_to_history(
            room_id="test_room",
            synthesis_text="Some synthesis",
        )

        assert result is None


# =========================================================================
# Test: update_room_summary
# =========================================================================


class TestUpdateRoomSummary:
    """Tests for RoomMemoryService.update_room_summary()."""

    @pytest.mark.asyncio
    async def test_extracts_and_persists_summary(
        self, room_memory, mock_db_service, mock_openai_service
    ):
        """Happy path: LLM extracts structured fields, summary is saved."""
        mock_db_service.get_room_memory_by_room_id.return_value = room_memory
        mock_db_service.update_room_memory_by_room_id.return_value = True
        mock_openai_service.call_supervisor_llm_json.return_value = {
            "current_goal": "Complete test coverage",
            "key_decisions": ["Use pytest", "Mock external services"],
            "open_questions": ["How to test async?"],
            "recent_agent_contributions": ["Agent A: found 3 bugs"],
            "important_constraints": ["Must finish by Friday"],
        }

        from services.memory_service import RoomMemoryService

        service = RoomMemoryService()
        service.database_service = mock_db_service
        service.openai_service = mock_openai_service

        success = await service.update_room_summary(
            room_id="test_room",
            synthesis_text="Agents found several issues...",
        )

        assert success is True
        saved_memory = mock_db_service.update_room_memory_by_room_id.call_args[0][1]
        assert saved_memory.room_summary.current_goal == "Complete test coverage"
        assert len(saved_memory.room_summary.key_decisions) == 2
        assert saved_memory.room_summary.last_updated_at is not None

    @pytest.mark.asyncio
    async def test_preserves_existing_on_llm_failure(
        self, room_memory, mock_db_service, mock_openai_service
    ):
        """On LLM failure, existing summary should be preserved (graceful degradation)."""
        mock_db_service.get_room_memory_by_room_id.return_value = room_memory
        mock_openai_service.call_supervisor_llm_json.side_effect = Exception(
            "LLM timeout"
        )

        from services.memory_service import RoomMemoryService

        service = RoomMemoryService()
        service.database_service = mock_db_service
        service.openai_service = mock_openai_service

        success = await service.update_room_summary(
            room_id="test_room",
            synthesis_text="Some text",
        )

        assert success is False
        mock_db_service.update_room_memory_by_room_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_false_when_room_not_found(
        self, mock_db_service, mock_openai_service
    ):
        """Should return False when room memory doesn't exist."""
        mock_db_service.get_room_memory_by_room_id.return_value = None

        from services.memory_service import RoomMemoryService

        service = RoomMemoryService()
        service.database_service = mock_db_service
        service.openai_service = mock_openai_service

        success = await service.update_room_summary(
            room_id="nonexistent",
            synthesis_text="Some text",
        )

        assert success is False

    @pytest.mark.asyncio
    async def test_keeps_existing_fields_when_extraction_returns_empty(
        self, room_memory, mock_db_service, mock_openai_service
    ):
        """If LLM returns empty/null fields, existing values should be kept."""
        room_memory.room_summary = RoomSummary(
            current_goal="Original goal",
            key_decisions=["Original decision"],
        )
        mock_db_service.get_room_memory_by_room_id.return_value = room_memory
        mock_db_service.update_room_memory_by_room_id.return_value = True
        mock_openai_service.call_supervisor_llm_json.return_value = {
            "current_goal": None,
            "key_decisions": [],
            "open_questions": ["New question"],
            "recent_agent_contributions": [],
            "important_constraints": [],
        }

        from services.memory_service import RoomMemoryService

        service = RoomMemoryService()
        service.database_service = mock_db_service
        service.openai_service = mock_openai_service

        success = await service.update_room_summary(
            room_id="test_room",
            synthesis_text="Some text",
        )

        assert success is True
        saved = mock_db_service.update_room_memory_by_room_id.call_args[0][1]
        assert saved.room_summary.current_goal == "Original goal"
        assert saved.room_summary.key_decisions == ["Original decision"]
        assert saved.room_summary.open_questions == ["New question"]

    @pytest.mark.asyncio
    async def test_populates_updated_after_turn_id(
        self, room_memory, mock_db_service, mock_openai_service
    ):
        """synthesis_turn_id should be stored in RoomSummary.updated_after_turn_id (§4.2)."""
        mock_db_service.get_room_memory_by_room_id.return_value = room_memory
        mock_db_service.update_room_memory_by_room_id.return_value = True
        mock_openai_service.call_supervisor_llm_json.return_value = {
            "current_goal": "Test goal",
            "key_decisions": [],
            "open_questions": [],
            "recent_agent_contributions": [],
            "important_constraints": [],
        }

        from services.memory_service import RoomMemoryService

        service = RoomMemoryService()
        service.database_service = mock_db_service
        service.openai_service = mock_openai_service

        success = await service.update_room_summary(
            room_id="test_room",
            synthesis_text="Some text",
            synthesis_turn_id="turn_abc_123",
        )

        assert success is True
        saved = mock_db_service.update_room_memory_by_room_id.call_args[0][1]
        assert saved.room_summary.updated_after_turn_id == "turn_abc_123"
        assert saved.room_summary.last_updated_at is not None


# =========================================================================
# Test: Prompt cache optimization
# =========================================================================


class TestPromptCacheOptimization:
    """Tests for §12.3: conversation_context moved to system prompt."""

    def test_conversation_context_in_system_prompt(self):
        """conversation_context placeholder should be in the system prompt template."""
        from services.room_supervisor_service import SUPERVISOR_V2_SYSTEM_PROMPT

        assert "{conversation_context}" in SUPERVISOR_V2_SYSTEM_PROMPT

    def test_conversation_context_not_in_user_prompt(self):
        """conversation_context placeholder should NOT be in the user prompt template."""
        from services.room_supervisor_service import SUPERVISOR_V2_USER_PROMPT

        assert "{conversation_context}" not in SUPERVISOR_V2_USER_PROMPT

    def test_user_prompt_has_only_dynamic_fields(self):
        """User prompt should only contain fields that change per iteration."""
        from services.room_supervisor_service import SUPERVISOR_V2_USER_PROMPT

        assert "{message_text}" in SUPERVISOR_V2_USER_PROMPT
        assert "{trajectory_summary}" in SUPERVISOR_V2_USER_PROMPT
        assert "{debate_mode_note}" in SUPERVISOR_V2_USER_PROMPT

    @pytest.mark.asyncio
    async def test_decide_next_passes_context_to_system_prompt(self):
        """decide_next() should format conversation_context into system prompt."""
        from services.room_supervisor_service import RoomSupervisorService
        from models.supervisor_v2 import (
            AgentProfile,
            RoomConfig,
            SupervisorTrajectory,
        )

        mock_openai = AsyncMock()
        service = RoomSupervisorService(openai_service=mock_openai)

        agents = [
            AgentProfile(
                agent_id="agent-1",
                agent_name="TestAgent",
                description="Test agent",
                is_healthy=True,
            )
        ]
        room_config = RoomConfig(is_debate_mode=False)
        trajectory = SupervisorTrajectory()

        valid_json = '{"action":"done","reasoning":"test","targets":[],"synthesis_instruction":null,"clarification_question":null}'

        with patch.object(service, "_call_supervisor_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {"action": "done", "reasoning": "test", "targets": [], "synthesis_instruction": None, "clarification_question": None}

            action = await service.decide_next(
                message_text="Test message",
                agent_registry=agents,
                room_config=room_config,
                trajectory=trajectory,
                conversation_context="This is the conversation background",
            )

            call_args = mock_llm.call_args
            system_prompt_arg = call_args.kwargs.get("system_prompt", call_args[0][0] if call_args[0] else "")
            user_prompt_arg = call_args.kwargs.get("user_prompt", call_args[0][1] if len(call_args[0]) > 1 else "")

            assert "This is the conversation background" in system_prompt_arg
            assert "This is the conversation background" not in user_prompt_arg


# =========================================================================
# Test: Compaction trigger on terminal statuses
# =========================================================================


class TestCompactionTrigger:
    """Tests for compaction trigger in _handle_v2_run_result() (§6.5)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        ["completed", "failed", "canceled"],
    )
    async def test_compaction_triggered_on_terminal_status(self, status):
        """Compaction should be awaited inline for all terminal statuses (§6.9)."""
        from modules.RoomMessageCenter import RoomMessageCenter

        rmc = RoomMessageCenter.__new__(RoomMessageCenter)
        rmc.database_service = AsyncMock()
        rmc.sse_manager = AsyncMock()
        rmc.sse_manager.remove_token = MagicMock()
        rmc.sse_manager.clear_cancellation = MagicMock()
        rmc.room_coordinator_service = AsyncMock()

        rmc._trigger_compaction_safe = AsyncMock()
        rmc._update_room_summary_safe = AsyncMock()

        from models.supervisor_v2 import SupervisorTrajectory

        trajectory = SupervisorTrajectory()

        result = MagicMock()
        result.status = status
        result.trajectory = trajectory
        result.synthesis_text = "Synthesis" if status == "completed" else None
        result.clarification_question = None

        user_message = MagicMock()
        user_message.extend_info = {}

        rmc.database_service.get_room_user_message_by_message_id.return_value = (
            user_message
        )
        rmc.database_service.update_room_user_message_by_message_id.return_value = True
        rmc.database_service.get_room_by_room_id.return_value = None
        rmc.database_service.cancel_descendants.return_value = None
        rmc.database_service.cancel_agent_messages_by_ids.return_value = None

        from modules.RoomMessageCenter import RunStatus

        await rmc._handle_v2_run_result(
            result=result,
            room_id="test_room",
            user_message_id="msg-1",
            user_message=user_message,
        )

        # Compaction is now awaited inline (not fire-and-forget) per §6.9
        rmc._trigger_compaction_safe.assert_awaited_once_with("test_room")

        # For completed status, room summary update is also awaited inline
        # (not fire-and-forget) to avoid a race with compaction.
        if status == "completed":
            rmc._update_room_summary_safe.assert_awaited_once()


# =========================================================================
# Test: MAX_CONTEXT_CHARS enforcement in ContextAssemblyService
# =========================================================================


class TestMaxContextCharsEnforcement:
    """Tests for MAX_CONTEXT_CHARS hard cap in ContextAssemblyService."""

    @pytest.fixture
    def service(self):
        """Create a ContextAssemblyService with mock settings."""
        with patch("models.context_config.settings") as mock_settings:
            mock_settings.context_model_window = 128000
            mock_settings.context_system_prompt_tokens = 2000
            mock_settings.context_tool_schema_tokens = 1000
            mock_settings.context_response_reserve_tokens = 4000
            mock_settings.context_room_pct = 0.2
            mock_settings.context_history_pct = 0.6
            mock_settings.context_task_pct = 0.2
            from services.context_assembly_service import ContextAssemblyService

            yield ContextAssemblyService()

    def test_supervisor_context_truncated_beyond_char_limit(self, service):
        """Context exceeding MAX_CONTEXT_CHARS should be hard-capped."""
        from common.utils.context_utils import MAX_CONTEXT_CHARS

        huge_content = "X" * (MAX_CONTEXT_CHARS + 5000)
        turns = [
            ConversationTurn(
                role=TurnRole.USER,
                content=huge_content,
                timestamp=datetime(2026, 2, 20),
            ),
        ]
        room_memory = RoomMemory(
            room_id="test_room",
            memory_content=MemoryContent(conversation_history=turns),
        )

        result = service.build_supervisor_context(
            room_memory=room_memory,
            current_task="Test",
        )

        assert len(result.context) <= MAX_CONTEXT_CHARS + 50
        assert result.was_truncated is True

    def test_agent_context_truncated_beyond_char_limit(self, service):
        """Agent context exceeding MAX_CONTEXT_CHARS should be hard-capped."""
        from common.utils.context_utils import MAX_CONTEXT_CHARS

        huge_content = "Y" * (MAX_CONTEXT_CHARS + 5000)
        turns = [
            ConversationTurn(
                role=TurnRole.USER,
                content=huge_content,
                timestamp=datetime(2026, 2, 20),
            ),
        ]
        room_memory = RoomMemory(
            room_id="test_room",
            memory_content=MemoryContent(conversation_history=turns),
        )

        result = service.build_agent_execution_context(
            room_memory=room_memory,
            current_task="Test",
            agent_name="TestAgent",
        )

        assert len(result.context) <= MAX_CONTEXT_CHARS + 50
        assert result.was_truncated is True
