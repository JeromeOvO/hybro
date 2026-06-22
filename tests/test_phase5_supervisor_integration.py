"""
Unit tests for Phase 5: Supervisor Integration.

Tests cover:
1. build_supervisor_context() wiring into _prepare_for_supervisor()
2. build_agent_execution_context() wiring into process_agent_message()
3. add_synthesis_to_history() in RoomMemoryService
4. update_room_summary() with LLM extraction
5. Compaction trigger in _handle_supervisor_run_result() for terminal statuses
6. Prompt cache optimization (conversation_context in system prompt)

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §11, §12.3, §18 Phase 5 for specification.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.utils.time import utcnow
from context_memory import assembly as context_memory_assembly
from context_memory.config import TokenBudgetConfig
from models.memory import (
    ConversationTurn,
    MemoryContent,
    RoomFact,
    RoomMemory,
    RoomSummary,
    TurnRole,
)
from models.supervisor import (
    ActionType,
    RunStatus,
    StepResult,
    SupervisorAction,
    SupervisorRunResult,
    SupervisorTrajectory,
    TrajectoryEntry,
)

# =========================================================================
# Fixtures
# =========================================================================


async def _noop_processing_status_emitter(**_kwargs):
    return None


class RecordingEventPublisher:
    def __init__(self):
        self.internal_events = []

    async def emit_internal(self, event, *, wait_for_local_handlers: bool = False):
        self.internal_events.append((event, wait_for_local_handlers))


class BoundRoomMemoryFacade:
    def __init__(self, service):
        self.service = service

    async def add_synthesis_to_history(self, room_id: str, synthesis_text: str, trajectory=None):
        from common.utils.context_utils import (
            LLM_TURN_NOTES_THRESHOLD,
            MAX_HISTORY_TURNS,
            MAX_SUMMARY_CHARS,
            estimate_tokens,
            extract_turn_notes,
        )
        from models.memory import ConversationTurn, TurnRole, TurnType

        enriched_content = synthesis_text
        if trajectory and trajectory.entries:
            agent_contributions = []
            for entry in trajectory.entries:
                for result in getattr(entry, "results", []):
                    if result.success and result.agent_name:
                        agent_contributions.append(
                            f"{result.agent_name}: {(result.task or '')[:100]}"
                        )
            if agent_contributions:
                enriched_content = (
                    f"{synthesis_text}\n\n"
                    f"[Agent contributions: {'; '.join(agent_contributions[:5])}]"
                )

        tokens_full = estimate_tokens(enriched_content)
        notes = extract_turn_notes(enriched_content)
        turn = ConversationTurn(
            role=TurnRole.SUPERVISOR,
            content=enriched_content,
            turn_type=TurnType.MESSAGE,
            estimated_tokens_full=tokens_full,
            turn_notes=notes,
        )
        summary_stub = (
            f"[Supervisor synthesis ({turn.turn_id[:8]})] "
            f"{enriched_content[:200]}..."
        )
        modified, matched = await self.service._store.push_and_trim_conversation_turn(
            room_id,
            turn.model_dump(mode="json"),
            max_turns=MAX_HISTORY_TURNS,
            summary_stub=summary_stub,
            max_summary_chars=MAX_SUMMARY_CHARS,
        )
        if not modified:
            return None
        if enriched_content and tokens_full > LLM_TURN_NOTES_THRESHOLD:
            asyncio.create_task(
                self.service._enrich_turn_notes_background(
                    room_id,
                    turn.turn_id,
                    notes,
                    enriched_content,
                )
            )
        return turn.turn_id

    async def update_room_summary(
        self,
        room_id: str,
        synthesis_text: str,
        synthesis_turn_id: str | None = None,
    ) -> bool:
        prompt = f"Synthesis:\n{synthesis_text}"
        try:
            extracted = await self.service.openai_service.call_supervisor_llm_json(
                system_prompt="You extract structured information from text. Respond with valid JSON only.",
                user_prompt=prompt,
                model="gpt-4o-mini",
            )
        except Exception:
            return False

        doc = await self.service._store.get_room_summary_projection(room_id)
        if not doc:
            return False

        existing = RoomSummary(**(doc.get("room_summary") or {}))
        new_summary = RoomSummary(
            current_goal=(
                extracted.get("current_goal")
                if extracted.get("current_goal") is not None
                else existing.current_goal
            ),
            key_decisions=(
                extracted.get("key_decisions")
                if extracted.get("key_decisions") is not None
                else existing.key_decisions
            ),
            open_questions=(
                extracted.get("open_questions")
                if extracted.get("open_questions") is not None
                else existing.open_questions
            ),
            recent_agent_contributions=(
                extracted.get("recent_agent_contributions")
                if extracted.get("recent_agent_contributions") is not None
                else existing.recent_agent_contributions
            ),
            important_constraints=(
                extracted.get("important_constraints")
                if extracted.get("important_constraints") is not None
                else existing.important_constraints
            ),
            last_updated_at=utcnow(),
            updated_after_turn_id=synthesis_turn_id or existing.updated_after_turn_id,
        )

        existing_fact_contents = {
            (fact.get("content") or "").lower().strip()
            for fact in (doc.get("room_facts") or [])
        }
        new_facts = []
        for fact_text in extracted.get("room_facts", []) or []:
            if not isinstance(fact_text, str) or not fact_text.strip():
                continue
            normalized = fact_text.lower().strip()
            if normalized in existing_fact_contents:
                continue
            new_facts.append(
                RoomFact(
                    content=fact_text.strip(),
                    source_turn_id=synthesis_turn_id,
                ).model_dump(mode="json")
            )
            existing_fact_contents.add(normalized)

        return await self.service._store.update_room_summary_atomic(
            room_id,
            new_summary.model_dump(mode="json"),
            new_facts=new_facts if new_facts else None,
            max_facts=50,
        )


def bind_room_memory_facade(service):
    service.bind_facade(BoundRoomMemoryFacade(service))
    return service


class BoundAssemblyFacade:
    def __init__(self, service):
        self.service = service

    def _budget(self):
        budget = self.service.budget
        return TokenBudgetConfig(
            model_context_window=budget.model_context_window,
            system_prompt=budget.system_prompt,
            tool_schemas=budget.tool_schemas,
            response_reserve=budget.response_reserve,
            room_context_pct=budget.room_context_pct,
            conversation_history_pct=budget.conversation_history_pct,
            current_task_pct=budget.current_task_pct,
        )

    def assemble_supervisor_context_from_memory(self, room_memory_doc, current_task, **kwargs):
        return context_memory_assembly.assemble_supervisor_context_from_memory(
            room_memory_doc,
            current_task,
            token_budget=self._budget(),
            **kwargs,
        )

    def assemble_agent_execution_context_from_memory(self, room_memory_doc, current_task, **kwargs):
        return context_memory_assembly.assemble_agent_execution_context_from_memory(
            room_memory_doc,
            current_task,
            token_budget=self._budget(),
            **kwargs,
        )


def bind_assembly_facade(service):
    service.bind_facade(BoundAssemblyFacade(service))
    return service


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
        """Synthesis text should be atomically pushed as a SUPERVISOR turn."""
        mock_db_service.push_and_trim_conversation_turn.return_value = (True, True)

        from app_shell.memory_service import RoomMemoryService

        service = bind_room_memory_facade(RoomMemoryService())
        service._store = mock_db_service
        service.openai_service = mock_openai_service

        result = await service.add_synthesis_to_history(
            room_id="test_room",
            synthesis_text="Combined results: Agent A found X, Agent B found Y.",
        )

        assert result is not None  # Returns turn_id on success
        mock_db_service.push_and_trim_conversation_turn.assert_awaited_once()

        pushed_turn = mock_db_service.push_and_trim_conversation_turn.call_args[0][1]
        assert pushed_turn["role"] == "supervisor"
        assert "Combined results" in pushed_turn["content"]

    @pytest.mark.asyncio
    async def test_returns_none_when_push_fails(
        self, mock_db_service, mock_openai_service
    ):
        """Should return None when room document doesn't exist."""
        mock_db_service.push_and_trim_conversation_turn.return_value = (False, False)

        from app_shell.memory_service import RoomMemoryService

        service = bind_room_memory_facade(RoomMemoryService())
        service._store = mock_db_service
        service.openai_service = mock_openai_service

        result = await service.add_synthesis_to_history(
            room_id="nonexistent",
            synthesis_text="Some synthesis",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_db_update_fails(
        self, room_memory, mock_db_service, mock_openai_service
    ):
        """Should return None when DB persistence fails."""
        mock_db_service.push_and_trim_conversation_turn.return_value = (False, True)

        from app_shell.memory_service import RoomMemoryService

        service = bind_room_memory_facade(RoomMemoryService())
        service._store = mock_db_service
        service.openai_service = mock_openai_service

        result = await service.add_synthesis_to_history(
            room_id="test_room",
            synthesis_text="Some synthesis",
        )

        assert result is None


class TestSynthesisLLMEnrichment:
    """Tests for background LLM enrichment of synthesis turn_notes (§6.2)."""

    @pytest.mark.asyncio
    async def test_enrichment_scheduled_for_long_synthesis(
        self, mock_db_service, mock_openai_service
    ):
        """Long synthesis text should trigger background _enrich_turn_notes_background."""
        mock_db_service.push_and_trim_conversation_turn.return_value = (True, True)

        from app_shell.memory_service import RoomMemoryService

        service = bind_room_memory_facade(RoomMemoryService())
        service._store = mock_db_service
        service.openai_service = mock_openai_service

        long_text = "This is a very detailed synthesis. " * 50

        with patch.object(
            service, "_enrich_turn_notes_background", new_callable=AsyncMock
        ) as mock_enrich:
            result = await service.add_synthesis_to_history(
                room_id="test_room",
                synthesis_text=long_text,
            )
            await asyncio.sleep(0)

            assert result is not None
            mock_enrich.assert_called_once()
            call_args = mock_enrich.call_args
            assert call_args[0][0] == "test_room"
            assert call_args[0][3] == long_text

    @pytest.mark.asyncio
    async def test_enrichment_skipped_for_short_synthesis(
        self, mock_db_service, mock_openai_service
    ):
        """Short synthesis text should NOT trigger background enrichment."""
        mock_db_service.push_and_trim_conversation_turn.return_value = (True, True)

        from app_shell.memory_service import RoomMemoryService

        service = bind_room_memory_facade(RoomMemoryService())
        service._store = mock_db_service
        service.openai_service = mock_openai_service

        with patch.object(
            service, "_enrich_turn_notes_background", new_callable=AsyncMock
        ) as mock_enrich:
            result = await service.add_synthesis_to_history(
                room_id="test_room",
                synthesis_text="Short synthesis.",
            )
            await asyncio.sleep(0)

            assert result is not None
            mock_enrich.assert_not_called()


# =========================================================================
# Test: update_room_summary
# =========================================================================


class TestUpdateRoomSummary:
    """Tests for RoomMemoryService.update_room_summary()."""

    @pytest.mark.asyncio
    async def test_extracts_and_persists_summary(
        self, room_memory, mock_db_service, mock_openai_service
    ):
        """Happy path: LLM extracts structured fields, summary is saved atomically."""
        mock_db_service.get_room_summary_projection.return_value = {
            "room_summary": {"current_goal": "Write tests"},
            "room_facts": [],
        }
        mock_db_service.update_room_summary_atomic.return_value = True
        mock_openai_service.call_supervisor_llm_json.return_value = {
            "current_goal": "Complete test coverage",
            "key_decisions": ["Use pytest", "Mock external services"],
            "open_questions": ["How to test async?"],
            "recent_agent_contributions": ["Agent A: found 3 bugs"],
            "important_constraints": ["Must finish by Friday"],
        }

        from app_shell.memory_service import RoomMemoryService

        service = bind_room_memory_facade(RoomMemoryService())
        service._store = mock_db_service
        service.openai_service = mock_openai_service

        success = await service.update_room_summary(
            room_id="test_room",
            synthesis_text="Agents found several issues...",
        )

        assert success is True
        mock_db_service.update_room_summary_atomic.assert_awaited_once()
        saved_summary = mock_db_service.update_room_summary_atomic.call_args[0][1]
        assert saved_summary["current_goal"] == "Complete test coverage"
        assert len(saved_summary["key_decisions"]) == 2

    @pytest.mark.asyncio
    async def test_preserves_existing_on_llm_failure(
        self, room_memory, mock_db_service, mock_openai_service
    ):
        """On LLM failure, existing summary should be preserved (graceful degradation)."""
        mock_openai_service.call_supervisor_llm_json.side_effect = Exception(
            "LLM timeout"
        )

        from app_shell.memory_service import RoomMemoryService

        service = bind_room_memory_facade(RoomMemoryService())
        service._store = mock_db_service
        service.openai_service = mock_openai_service

        success = await service.update_room_summary(
            room_id="test_room",
            synthesis_text="Some text",
        )

        assert success is False
        mock_db_service.update_room_summary_atomic.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_false_when_room_not_found(
        self, mock_db_service, mock_openai_service
    ):
        """Should return False when room memory doesn't exist."""
        mock_db_service.get_room_summary_projection.return_value = None
        mock_openai_service.call_supervisor_llm_json.return_value = {
            "current_goal": "Test",
            "key_decisions": [],
            "open_questions": [],
            "recent_agent_contributions": [],
            "important_constraints": [],
        }

        from app_shell.memory_service import RoomMemoryService

        service = bind_room_memory_facade(RoomMemoryService())
        service._store = mock_db_service
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
        mock_db_service.get_room_summary_projection.return_value = {
            "room_summary": {
                "current_goal": "Original goal",
                "key_decisions": ["Original decision"],
            },
            "room_facts": [],
        }
        mock_db_service.update_room_summary_atomic.return_value = True
        mock_openai_service.call_supervisor_llm_json.return_value = {
            "current_goal": None,
            "key_decisions": [],
            "open_questions": ["New question"],
            "recent_agent_contributions": [],
            "important_constraints": [],
        }

        from app_shell.memory_service import RoomMemoryService

        service = bind_room_memory_facade(RoomMemoryService())
        service._store = mock_db_service
        service.openai_service = mock_openai_service

        success = await service.update_room_summary(
            room_id="test_room",
            synthesis_text="Some text",
        )

        assert success is True
        saved_summary = mock_db_service.update_room_summary_atomic.call_args[0][1]
        assert saved_summary["current_goal"] == "Original goal"
        assert saved_summary["key_decisions"] == []  # LLM explicitly returned empty list
        assert saved_summary["open_questions"] == ["New question"]

    @pytest.mark.asyncio
    async def test_populates_updated_after_turn_id(
        self, room_memory, mock_db_service, mock_openai_service
    ):
        """synthesis_turn_id should be stored in RoomSummary.updated_after_turn_id (§4.2)."""
        mock_db_service.get_room_summary_projection.return_value = {
            "room_summary": {},
            "room_facts": [],
        }
        mock_db_service.update_room_summary_atomic.return_value = True
        mock_openai_service.call_supervisor_llm_json.return_value = {
            "current_goal": "Test goal",
            "key_decisions": [],
            "open_questions": [],
            "recent_agent_contributions": [],
            "important_constraints": [],
        }

        from app_shell.memory_service import RoomMemoryService

        service = bind_room_memory_facade(RoomMemoryService())
        service._store = mock_db_service
        service.openai_service = mock_openai_service

        success = await service.update_room_summary(
            room_id="test_room",
            synthesis_text="Some text",
            synthesis_turn_id="turn_abc_123",
        )

        assert success is True
        saved_summary = mock_db_service.update_room_summary_atomic.call_args[0][1]
        assert saved_summary["updated_after_turn_id"] == "turn_abc_123"
        assert saved_summary["last_updated_at"] is not None


# =========================================================================
# Test: Prompt cache optimization
# =========================================================================


class TestPromptCacheOptimization:
    """Tests for §12.3: conversation_context moved to system prompt."""

    def test_conversation_context_in_system_prompt(self):
        """conversation_context placeholder should be in the system prompt template."""
        from execution.orchestration.room_supervisor_service import (
            SUPERVISOR_SYSTEM_PROMPT,
        )

        assert "{conversation_context}" in SUPERVISOR_SYSTEM_PROMPT

    def test_conversation_context_not_in_user_prompt(self):
        """conversation_context placeholder should NOT be in the user prompt template."""
        from execution.orchestration.room_supervisor_service import (
            SUPERVISOR_USER_PROMPT,
        )

        assert "{conversation_context}" not in SUPERVISOR_USER_PROMPT

    def test_user_prompt_has_only_dynamic_fields(self):
        """User prompt should only contain fields that change per iteration."""
        from execution.orchestration.room_supervisor_service import (
            SUPERVISOR_USER_PROMPT,
        )

        assert "{message_text}" in SUPERVISOR_USER_PROMPT
        assert "{trajectory_summary}" in SUPERVISOR_USER_PROMPT
        assert "{debate_mode_note}" in SUPERVISOR_USER_PROMPT
        assert "{steps_completed}" in SUPERVISOR_USER_PROMPT
        assert "{max_steps}" in SUPERVISOR_USER_PROMPT
        assert "{steps_remaining}" in SUPERVISOR_USER_PROMPT
        assert "{budget_warning}" in SUPERVISOR_USER_PROMPT
        assert "{quoted_section}" in SUPERVISOR_USER_PROMPT

    @pytest.mark.asyncio
    async def test_decide_next_includes_quoted_text_in_user_prompt(self):
        """decide_next() should include verbatim quoted text in the user prompt."""
        from execution.orchestration.room_supervisor_service import (
            RoomSupervisorService,
        )
        from models.supervisor import AgentProfile, RoomConfig, SupervisorTrajectory

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

        with patch.object(service, "_call_supervisor_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "action": "done",
                "reasoning": "test",
                "targets": [],
                "synthesis_instruction": None,
                "clarification_question": None,
            }

            await service.decide_next(
                message_text="Get details about this",
                agent_registry=agents,
                room_config=room_config,
                trajectory=trajectory,
                quoted_text="line one\nline two",
            )

            user_prompt_arg = mock_llm.call_args.kwargs.get(
                "user_prompt",
                mock_llm.call_args[0][1] if len(mock_llm.call_args[0]) > 1 else "",
            )
            assert "line one\nline two" in user_prompt_arg
            assert "## Quoted text" in user_prompt_arg

    @pytest.mark.asyncio
    async def test_decide_next_passes_context_to_system_prompt(self):
        """decide_next() should format conversation_context into system prompt."""
        from execution.orchestration.room_supervisor_service import (
            RoomSupervisorService,
        )
        from models.supervisor import (
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

        with patch.object(service, "_call_supervisor_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {"action": "done", "reasoning": "test", "targets": [], "synthesis_instruction": None, "clarification_question": None}

            await service.decide_next(
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
    """Tests for compaction trigger in _handle_supervisor_run_result() (§6.5)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        ["completed", "failed", "canceled"],
    )
    async def test_compaction_triggered_on_terminal_status(self, status):
        """Compaction should be awaited inline for all terminal statuses (§6.9)."""
        from execution.orchestration.room_message_center import RoomMessageCenter

        rmc = RoomMessageCenter.__new__(RoomMessageCenter)
        rmc.message_reader = AsyncMock()
        rmc.message_writer = AsyncMock()
        rmc.room_reader = AsyncMock()
        rmc.delivery = AsyncMock()
        rmc.delivery.remove_token = MagicMock()
        rmc.delivery.clear_cancellation = MagicMock()
        rmc.coordinator = AsyncMock()
        rmc._processing_status_emitter = _noop_processing_status_emitter

        rmc._trigger_compaction_safe = AsyncMock()
        rmc._update_room_summary_safe = AsyncMock()

        from models.supervisor import SupervisorTrajectory

        trajectory = SupervisorTrajectory()

        result = MagicMock()
        result.status = status
        result.trajectory = trajectory
        result.synthesis_text = "Synthesis" if status == "completed" else None
        result.clarification_question = None

        user_message = MagicMock()
        user_message.extend_info = {}

        rmc.message_reader.get_room_user_message_by_message_id.return_value = (
            user_message
        )
        rmc.message_writer.update_room_user_message_by_message_id.return_value = True
        rmc.room_reader.get_room_by_room_id.return_value = None
        rmc.message_writer.cancel_descendants.return_value = None
        rmc.message_writer.cancel_agent_messages_by_ids.return_value = None


        mock_memory_service = AsyncMock()
        mock_memory_service.add_synthesis_to_history.return_value = "turn_synth_123"
        rmc.room_memory = mock_memory_service

        await rmc._handle_supervisor_run_result(
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
            from app_shell.context_assembly_service import ContextAssemblyService

            yield bind_assembly_facade(ContextAssemblyService())

    def test_supervisor_context_truncated_beyond_char_limit(self, service):
        """Context exceeding MAX_CONTEXT_CHARS should be hard-capped."""
        small_cap = 1_000
        with patch("context_memory.assembly.MAX_CONTEXT_CHARS", small_cap):
            huge_content = "X" * (small_cap + 500)
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

            assert len(result.context) <= small_cap + 50
            assert result.was_truncated is True

    def test_agent_context_truncated_beyond_char_limit(self, service):
        """Agent context exceeding MAX_CONTEXT_CHARS should be hard-capped."""
        small_cap = 1_000
        with patch("context_memory.assembly.MAX_CONTEXT_CHARS", small_cap):
            huge_content = "Y" * (small_cap + 500)
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

            assert len(result.context) <= small_cap + 50
            assert result.was_truncated is True


# =========================================================================
# Test: _parse_supervisor_action case-insensitive parsing
# =========================================================================


class TestParseV2ActionCaseInsensitive:
    """Tests for case-insensitive action parsing in _parse_supervisor_action.

    The LLM may return action strings in any case (DELEGATE, delegate, Delegate).
    The parser must normalize to lowercase before matching the ActionType enum.
    """

    @pytest.fixture
    def service(self):
        from execution.orchestration.room_supervisor_service import (
            RoomSupervisorService,
        )
        mock_openai = MagicMock()
        return RoomSupervisorService(openai_service=mock_openai)

    @pytest.mark.parametrize("action_str,expected_action", [
        ("delegate", "delegate"),
        ("DELEGATE", "delegate"),
        ("Delegate", "delegate"),
        ("synthesize", "synthesize"),
        ("SYNTHESIZE", "synthesize"),
        ("clarify", "clarify"),
        ("CLARIFY", "clarify"),
        ("done", "done"),
        ("DONE", "done"),
        ("Done", "done"),
    ])
    def test_parses_any_case(self, service, action_str, expected_action):
        """Action strings in any case should be recognized."""
        from models.supervisor import ActionType

        response_json = {
            "action": action_str,
            "reasoning": "test",
            "targets": [],
            "synthesis_instruction": None,
            "clarification_question": None,
        }
        action = service._parse_supervisor_action(response_json)
        assert action.action == ActionType(expected_action)

    def test_unknown_action_defaults_to_done(self, service):
        """Unrecognized action strings should default to DONE."""
        from models.supervisor import ActionType

        response_json = {
            "action": "INVALID_ACTION",
            "reasoning": "test",
            "targets": [],
        }
        action = service._parse_supervisor_action(response_json)
        assert action.action == ActionType.DONE

    def test_missing_action_defaults_to_done(self, service):
        """Missing action key should default to DONE."""
        from models.supervisor import ActionType

        response_json = {
            "reasoning": "test",
            "targets": [],
        }
        action = service._parse_supervisor_action(response_json)
        assert action.action == ActionType.DONE

    def test_delegate_with_targets(self, service):
        """DELEGATE (uppercase) with targets should parse targets correctly."""
        from models.supervisor import ActionType

        response_json = {
            "action": "DELEGATE",
            "reasoning": "Send to agent",
            "targets": [
                {"agent_id": "agent-1", "agent_name": "TestAgent", "task": "Do something"},
            ],
        }
        action = service._parse_supervisor_action(response_json)
        assert action.action == ActionType.DELEGATE
        assert len(action.targets) == 1
        assert action.targets[0].agent_id == "agent-1"
        assert action.targets[0].task == "Do something"

    def test_null_action_defaults_to_done(self, service):
        """LLM returning null for action should default to DONE."""
        from models.supervisor import ActionType

        response_json = {"action": None, "reasoning": "test", "targets": []}
        action = service._parse_supervisor_action(response_json)
        assert action.action == ActionType.DONE

    def test_numeric_action_defaults_to_done(self, service):
        """LLM returning a number for action should default to DONE."""
        from models.supervisor import ActionType

        response_json = {"action": 42, "reasoning": "test", "targets": []}
        action = service._parse_supervisor_action(response_json)
        assert action.action == ActionType.DONE

    def test_object_action_defaults_to_done(self, service):
        """LLM returning an object for action should default to DONE."""
        from models.supervisor import ActionType

        response_json = {
            "action": {"type": "delegate"},
            "reasoning": "test",
            "targets": [],
        }
        action = service._parse_supervisor_action(response_json)
        assert action.action == ActionType.DONE


# =========================================================================
# Test: _parse_supervisor_action prompt_type/choices sanitization
# =========================================================================


class TestParseV2ActionClarifySanitization:
    """Tests that _parse_supervisor_action sanitizes prompt_type and choices from LLM output."""

    @pytest.fixture
    def service(self):
        from execution.orchestration.room_supervisor_service import (
            RoomSupervisorService,
        )
        mock_openai = MagicMock()
        return RoomSupervisorService(openai_service=mock_openai)

    def _clarify_json(self, **overrides):
        base = {
            "action": "clarify",
            "reasoning": "need info",
            "targets": [],
            "clarification_question": "Which one?",
        }
        base.update(overrides)
        return base

    def test_valid_prompt_type_text(self, service):
        action = service._parse_supervisor_action(self._clarify_json(prompt_type="text"))
        assert action.prompt_type == "text"

    def test_valid_prompt_type_choice_with_choices(self, service):
        action = service._parse_supervisor_action(
            self._clarify_json(prompt_type="choice", choices=["A", "B", "C"])
        )
        assert action.prompt_type == "choice"
        assert action.choices == ["A", "B", "C"]

    def test_valid_prompt_type_confirmation(self, service):
        action = service._parse_supervisor_action(self._clarify_json(prompt_type="confirmation"))
        assert action.prompt_type == "confirmation"

    def test_invalid_prompt_type_number_becomes_none(self, service):
        action = service._parse_supervisor_action(self._clarify_json(prompt_type=42))
        assert action.prompt_type is None

    def test_invalid_prompt_type_unknown_string_becomes_none(self, service):
        action = service._parse_supervisor_action(self._clarify_json(prompt_type="multiple_choice"))
        assert action.prompt_type is None

    def test_choices_non_list_becomes_none(self, service):
        action = service._parse_supervisor_action(self._clarify_json(choices="not a list"))
        assert action.choices is None

    def test_choices_list_with_non_strings_becomes_none(self, service):
        action = service._parse_supervisor_action(self._clarify_json(choices=["ok", 123, None]))
        assert action.choices is None

    def test_missing_prompt_type_is_none(self, service):
        action = service._parse_supervisor_action(self._clarify_json())
        assert action.prompt_type is None
        assert action.choices is None


class TestTrajectoryStatusSerialization:
    """Verify that SupervisorTrajectory serializes cleanly when status is set
    via TrajectoryStatus enum members (not raw strings).

    Regression test for the Pydantic serialization warning:
    'Expected `enum` - serialized value may not be as expected'
    """

    def test_enum_status_serializes_without_warning(self):
        """model_dump(mode='json') with enum status should not warn."""
        import warnings

        from models.supervisor import (
            SupervisorTrajectory,
            TrajectoryStatus,
        )

        trajectory = SupervisorTrajectory()
        trajectory.status = TrajectoryStatus.COMPLETED

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            data = trajectory.model_dump(mode="json")
            pydantic_warnings = [
                x for x in w
                if "PydanticSerializationUnexpectedValue" in str(x.message)
            ]
            assert len(pydantic_warnings) == 0

        assert data["status"] == "completed"

    @pytest.mark.parametrize("status", [
        "completed", "failed", "canceled", "running", "awaiting_input",
    ])
    def test_all_statuses_roundtrip_cleanly(self, status):
        """Every TrajectoryStatus value should serialize and deserialize."""
        import warnings

        from models.supervisor import (
            SupervisorTrajectory,
            TrajectoryStatus,
        )

        trajectory = SupervisorTrajectory()
        trajectory.status = TrajectoryStatus(status)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            data = trajectory.model_dump(mode="json")
            pydantic_warnings = [
                x for x in w
                if "PydanticSerializationUnexpectedValue" in str(x.message)
            ]
            assert len(pydantic_warnings) == 0

        assert data["status"] == status

        restored = SupervisorTrajectory.model_validate(data)
        assert restored.status == TrajectoryStatus(status)

    def test_raw_string_triggers_pydantic_warning(self):
        """Assigning a raw string (not enum) to status should trigger a
        Pydantic serialization warning — proving the old code was broken."""
        import warnings

        from models.supervisor import SupervisorTrajectory

        trajectory = SupervisorTrajectory()
        trajectory.status = "completed"  # type: ignore[assignment]

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            trajectory.model_dump(mode="json")
            pydantic_warnings = [
                x for x in w
                if "PydanticSerializationUnexpectedValue" in str(x.message)
            ]
            assert len(pydantic_warnings) > 0, (
                "Expected Pydantic warning when status is a raw string, "
                "not a TrajectoryStatus enum member"
            )


# =========================================================================
# Tests: _handle_supervisor_run_result — supervisor synthesis vs default summary
# =========================================================================


class TestHandleV2RunResultUnifiedSummary:
    """Verify that _handle_supervisor_run_result routes to _emit_unified_summary
    with correct arguments for both synthesis and non-synthesis paths."""

    @pytest.fixture
    def rmc(self):
        """Build a RoomMessageCenter with key collaborators mocked."""
        with (
            patch("execution.orchestration.room_message_center.default_store") as mock_db,
            patch("execution.orchestration.room_message_center.delivery") as mock_delivery,
            patch("execution.orchestration.room_message_center.coordinator"),
            patch("execution.orchestration.room_message_center.room_runtime"),
            patch("execution.orchestration.room_message_center.notification_service"),
            patch("execution.orchestration.room_message_center.a2a_transport"),
            patch("execution.orchestration.room_message_center.remote_task_reader"),
            patch("execution.orchestration.room_message_center.agent_resolver_service"),
            patch("execution.orchestration.room_message_center.room_memory"),
            patch("execution.orchestration.room_message_center.room_supervisor_service"),
            patch("execution.orchestration.room_message_center.rate_limit_service"),
            patch("execution.orchestration.room_message_center.debate_service"),
        ):
            mock_db.get_room_user_message_by_message_id = AsyncMock(return_value=None)
            mock_db.update_room_user_message_by_message_id = AsyncMock()
            mock_delivery.send_processing_status = AsyncMock()
            mock_delivery.remove_token = MagicMock()

            from execution.orchestration.factory import create_room_message_center

            rmc = create_room_message_center(
                debate_rounds=2,
                event_publisher=RecordingEventPublisher(),
            )
            rmc._emit_unified_summary = AsyncMock(return_value=("synthesis", "Final synthesis."))
            rmc._emit_deterministic_digest = AsyncMock()
            rmc._trigger_compaction_safe = AsyncMock()
            rmc._processing_status_emitter = _noop_processing_status_emitter
            yield rmc

    @pytest.fixture
    def completed_result_with_synthesis(self):
        return SupervisorRunResult(
            status=RunStatus.COMPLETED,
            trajectory=SupervisorTrajectory(),
            synthesis_text="Final synthesis.",
        )

    @pytest.fixture
    def completed_result_without_synthesis(self):
        return SupervisorRunResult(
            status=RunStatus.COMPLETED,
            trajectory=SupervisorTrajectory(),
            synthesis_text=None,
        )

    @pytest.mark.asyncio
    async def test_synthesis_text_passed_to_unified_summary(
        self, rmc, completed_result_with_synthesis
    ):
        """When supervisor produces synthesis, it is passed directly."""
        await rmc._handle_supervisor_run_result(
            result=completed_result_with_synthesis,
            room_id="room-1",
            user_message_id="msg-1",
        )
        rmc._emit_unified_summary.assert_awaited_once()
        call_kwargs = rmc._emit_unified_summary.call_args
        assert call_kwargs[0] == ("room-1", "msg-1")
        assert call_kwargs[1]["synthesis_text"] == "Final synthesis."

    @pytest.mark.asyncio
    async def test_no_synthesis_emits_deterministic_digest_for_multi_agent_done(
        self, rmc, completed_result_without_synthesis
    ):
        """When supervisor chose DONE with 2+ agents, emit deterministic digest (not LLM summary)."""
        from datetime import datetime

        from models.supervisor import (
            ActionType,
            StepResult,
            SupervisorAction,
            TrajectoryEntry,
        )

        completed_result_without_synthesis.trajectory.entries = [
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="delegate",
                ),
                started_at=datetime(2026, 1, 1),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent A",
                        task="Answer",
                        success=True,
                        response_text="Answer A",
                    ),
                    StepResult(
                        step_number=2,
                        agent_id="agent-2",
                        agent_name="Agent B",
                        task="Answer",
                        success=True,
                        response_text="Answer B",
                    ),
                ],
            ),
        ]
        await rmc._handle_supervisor_run_result(
            result=completed_result_without_synthesis,
            room_id="room-1",
            user_message_id="msg-1",
        )
        rmc._emit_unified_summary.assert_not_awaited()
        rmc._emit_deterministic_digest.assert_awaited_once_with(
            "room-1",
            "msg-1",
            agent_count=2,
        )

    @pytest.mark.asyncio
    async def test_no_synthesis_skips_digest_for_single_agent_done(
        self, rmc, completed_result_without_synthesis
    ):
        """DONE with fewer than 2 agent responses does not emit deterministic digest."""
        await rmc._handle_supervisor_run_result(
            result=completed_result_without_synthesis,
            room_id="room-1",
            user_message_id="msg-1",
        )
        rmc._emit_unified_summary.assert_not_awaited()
        rmc._emit_deterministic_digest.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_trajectory_responses_extracted_and_passed(self, rmc):
        """Trajectory responses are extracted from DELEGATE entries and
        forwarded to _emit_unified_summary."""
        from datetime import datetime

        delegate_action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="Debate: delegate to both agents",
        )
        entry = TrajectoryEntry(
            step_number=1,
            action=delegate_action,
            started_at=datetime(2026, 1, 1),
            results=[
                StepResult(
                    step_number=1,
                    agent_id="agent-1",
                    agent_name="Agent Alpha",
                    task="Answer the question",
                    response_text="Alpha's answer here.",
                    success=True,
                ),
                StepResult(
                    step_number=2,
                    agent_id="agent-2",
                    agent_name="Agent Beta",
                    task="Answer the question",
                    response_text="Beta's answer here.",
                    success=True,
                ),
            ],
        )
        result = SupervisorRunResult(
            status=RunStatus.COMPLETED,
            trajectory=SupervisorTrajectory(entries=[entry]),
            synthesis_text="Combined synthesis.",
        )
        await rmc._handle_supervisor_run_result(
            result=result,
            room_id="room-1",
            user_message_id="msg-1",
        )
        rmc._emit_unified_summary.assert_awaited_once()
        call_kwargs = rmc._emit_unified_summary.call_args
        assert call_kwargs[1]["trajectory_responses"] == [
            {"agent_name": "Agent Alpha", "message": "Alpha's answer here."},
            {"agent_name": "Agent Beta", "message": "Beta's answer here."},
        ]


# =========================================================================
# Test: _parse_supervisor_action multi-question CLARIFY parsing
# =========================================================================


class TestParseV2ActionMultiQuestion:
    """Tests that _parse_supervisor_action correctly parses the questions array."""

    @pytest.fixture
    def service(self):
        from execution.orchestration.room_supervisor_service import (
            RoomSupervisorService,
        )
        return RoomSupervisorService(
            openai_service=MagicMock(),
            store=MagicMock(),
        )

    def test_parses_valid_questions_array(self, service):
        action = service._parse_supervisor_action({
            "action": "clarify",
            "reasoning": "need more info",
            "questions": [
                {"prompt": "Travel dates?", "prompt_type": "text"},
                {"prompt": "Budget?", "prompt_type": "choice", "choices": ["Low", "High"]},
                {"prompt": "Proceed?", "prompt_type": "confirmation"},
            ],
        })
        assert action.questions is not None
        assert len(action.questions) == 3
        assert action.questions[0].prompt == "Travel dates?"
        assert action.questions[0].prompt_type == "text"
        assert action.questions[1].choices == ["Low", "High"]
        assert action.questions[2].prompt_type == "confirmation"

    def test_falls_back_to_clarification_question_when_no_questions(self, service):
        action = service._parse_supervisor_action({
            "action": "clarify",
            "reasoning": "need info",
            "clarification_question": "What dates?",
        })
        assert action.questions is None
        assert action.clarification_question == "What dates?"

    def test_ignores_questions_with_invalid_prompts(self, service):
        action = service._parse_supervisor_action({
            "action": "clarify",
            "reasoning": "need info",
            "questions": [
                {"prompt": 123},
                {"prompt": "Valid question?", "prompt_type": "text"},
            ],
        })
        assert action.questions is not None
        assert len(action.questions) == 1
        assert action.questions[0].prompt == "Valid question?"

    def test_sanitizes_invalid_prompt_type_in_questions(self, service):
        action = service._parse_supervisor_action({
            "action": "clarify",
            "reasoning": "need info",
            "questions": [
                {"prompt": "Q1?", "prompt_type": "invalid_type"},
                {"prompt": "Q2?", "prompt_type": "choice", "choices": "not a list"},
            ],
        })
        assert action.questions is not None
        assert len(action.questions) == 2
        assert action.questions[0].prompt_type is None
        assert action.questions[1].choices is None

    def test_empty_questions_array_yields_none(self, service):
        action = service._parse_supervisor_action({
            "action": "clarify",
            "reasoning": "need info",
            "questions": [],
        })
        assert action.questions is None

    def test_questions_array_not_a_list_yields_none(self, service):
        action = service._parse_supervisor_action({
            "action": "clarify",
            "reasoning": "need info",
            "questions": "not a list",
        })
        assert action.questions is None
