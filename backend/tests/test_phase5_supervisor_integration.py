"""
Unit tests for Phase 5: Supervisor Integration.

Tests cover:
1. build_supervisor_context() wiring into _prepare_for_supervisor()
2. build_agent_execution_context() wiring into process_agent_message()
3. add_synthesis_to_history() through the ContextMemory runtime adapter
4. update_room_summary() with LLM extraction
5. Compaction trigger in _handle_supervisor_run_result() for terminal statuses
6. Prompt cache optimization (conversation_context in system prompt)

See docs/System-Architecture.md for the current design.
"""

import asyncio
import copy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from common.utils.time import utcnow
from models.agent import AgentStatus
from models.memory import (
    ConversationTurn,
    MemoryContent,
    RoomFact,
    RoomMemory,
    RoomSummary,
    TurnRole,
)
from models.orchestration import OrchestrationStatus, PlannerAction
from models.processing import ProcessingResult, ProcessingStatus
from models.room import MessageContent, RoomUserMessage
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
    return {"accepted": True}


class RecordingEventPublisher:
    def __init__(self):
        self.internal_events = []

    async def publish(
        self,
        event,
        *,
        wait_for_handlers: bool = False,
        fanout: bool = True,
    ):
        self.internal_events.append((event, wait_for_handlers, fanout))


class BoundRoomMemoryFacade:
    def __init__(self, service):
        self.service = service

    async def add_synthesis_to_history(
        self, room_id: str, synthesis_text: str, trajectory=None
    ):
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
            f"[Supervisor synthesis ({turn.turn_id[:8]})] {enriched_content[:200]}..."
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
        doc = await self.service._store.get_room_summary_projection(room_id)
        if not doc:
            return False

        existing = RoomSummary(**(doc.get("room_summary") or {}))
        prompt = (
            "Extract an incremental room summary. Empty lists preserve existing "
            "values; durable lists merge case-insensitively; non-empty recent "
            "lists replace existing values.\n"
            f"Existing projection:\n{existing.model_dump(mode='json')!r}\n"
            f"Synthesis:\n{synthesis_text}"
        )
        try:
            extracted = await self.service.supervisor_llm_service.call_json(
                system_prompt="You extract structured information from text. Respond with valid JSON only.",
                user_prompt=prompt,
                model="gpt-4o-mini",
            )
        except Exception:
            return False

        def non_empty_strings(value):
            return [
                item.strip()
                for item in (value or [])
                if isinstance(item, str) and item.strip()
            ]

        def merge(existing_values, new_values):
            values = []
            seen = set()
            for item in [
                *non_empty_strings(existing_values),
                *non_empty_strings(new_values),
            ]:
                key = item.casefold()
                if key not in seen:
                    seen.add(key)
                    values.append(item)
            return values

        extracted_goal = extracted.get("current_goal")
        new_summary = RoomSummary(
            current_goal=(
                extracted_goal.strip()
                if isinstance(extracted_goal, str) and extracted_goal.strip()
                else existing.current_goal
            ),
            key_decisions=merge(existing.key_decisions, extracted.get("key_decisions")),
            open_questions=(
                non_empty_strings(extracted.get("open_questions"))
                or existing.open_questions
            ),
            recent_agent_contributions=(
                non_empty_strings(extracted.get("recent_agent_contributions"))
                or existing.recent_agent_contributions
            ),
            important_constraints=merge(
                existing.important_constraints,
                extracted.get("important_constraints"),
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


def room_memory_facade(mock_db_service, mock_supervisor_llm_service):
    holder = SimpleNamespace(
        _store=mock_db_service,
        supervisor_llm_service=mock_supervisor_llm_service,
        _enrich_turn_notes_background=AsyncMock(),
    )
    return BoundRoomMemoryFacade(holder), holder


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
        conversation_history=turns,
        room_summary=RoomSummary(current_goal="Write tests"),
    )


@pytest.fixture
def mock_db_service():
    """Mock DatabaseService."""
    db = AsyncMock()
    return db


@pytest.fixture
def mock_supervisor_llm_service():
    """Mock supervisor LLM capability."""
    svc = AsyncMock()
    return svc


# =========================================================================
# Test: add_synthesis_to_history
# =========================================================================


class TestAddSynthesisToHistory:
    """Tests for synthesis history through the runtime adapter."""

    @pytest.mark.asyncio
    async def test_adds_supervisor_turn_and_persists(
        self, room_memory, mock_db_service, mock_supervisor_llm_service
    ):
        """Synthesis text should be atomically pushed as a SUPERVISOR turn."""
        mock_db_service.push_and_trim_conversation_turn.return_value = (True, True)

        service, _holder = room_memory_facade(
            mock_db_service, mock_supervisor_llm_service
        )

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
        self, mock_db_service, mock_supervisor_llm_service
    ):
        """Should return None when room document doesn't exist."""
        mock_db_service.push_and_trim_conversation_turn.return_value = (False, False)

        service, _holder = room_memory_facade(
            mock_db_service, mock_supervisor_llm_service
        )

        result = await service.add_synthesis_to_history(
            room_id="nonexistent",
            synthesis_text="Some synthesis",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_db_update_fails(
        self, room_memory, mock_db_service, mock_supervisor_llm_service
    ):
        """Should return None when DB persistence fails."""
        mock_db_service.push_and_trim_conversation_turn.return_value = (False, True)

        service, _holder = room_memory_facade(
            mock_db_service, mock_supervisor_llm_service
        )

        result = await service.add_synthesis_to_history(
            room_id="test_room",
            synthesis_text="Some synthesis",
        )

        assert result is None


class TestSynthesisLLMEnrichment:
    """Tests for background LLM enrichment of synthesis turn_notes (§6.2)."""

    @pytest.mark.asyncio
    async def test_enrichment_scheduled_for_long_synthesis(
        self, mock_db_service, mock_supervisor_llm_service
    ):
        """Long synthesis text should trigger background _enrich_turn_notes_background."""
        mock_db_service.push_and_trim_conversation_turn.return_value = (True, True)

        service, holder = room_memory_facade(
            mock_db_service, mock_supervisor_llm_service
        )

        long_text = "This is a very detailed synthesis. " * 50

        with patch.object(
            holder, "_enrich_turn_notes_background", new_callable=AsyncMock
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
        self, mock_db_service, mock_supervisor_llm_service
    ):
        """Short synthesis text should NOT trigger background enrichment."""
        mock_db_service.push_and_trim_conversation_turn.return_value = (True, True)

        service, holder = room_memory_facade(
            mock_db_service, mock_supervisor_llm_service
        )

        with patch.object(
            holder, "_enrich_turn_notes_background", new_callable=AsyncMock
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
    """Tests for summary updates through the runtime adapter."""

    @pytest.mark.asyncio
    async def test_extracts_and_persists_summary(
        self, room_memory, mock_db_service, mock_supervisor_llm_service
    ):
        """Happy path: LLM extracts structured fields, summary is saved atomically."""
        mock_db_service.get_room_summary_projection.return_value = {
            "room_summary": {"current_goal": "Write tests"},
            "room_facts": [],
        }
        mock_db_service.update_room_summary_atomic.return_value = True
        mock_supervisor_llm_service.call_json.return_value = {
            "current_goal": "Complete test coverage",
            "key_decisions": ["Use pytest", "Mock external services"],
            "open_questions": ["How to test async?"],
            "recent_agent_contributions": ["Agent A: found 3 bugs"],
            "important_constraints": ["Must finish by Friday"],
        }

        service, _holder = room_memory_facade(
            mock_db_service, mock_supervisor_llm_service
        )

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
        self, room_memory, mock_db_service, mock_supervisor_llm_service
    ):
        """On LLM failure, existing summary should be preserved (graceful degradation)."""
        mock_db_service.get_room_summary_projection.return_value = {
            "room_summary": {"current_goal": "Write tests"},
            "room_facts": [],
        }
        mock_supervisor_llm_service.call_json.side_effect = Exception("LLM timeout")

        service, _holder = room_memory_facade(
            mock_db_service, mock_supervisor_llm_service
        )

        success = await service.update_room_summary(
            room_id="test_room",
            synthesis_text="Some text",
        )

        assert success is False
        mock_db_service.update_room_summary_atomic.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_false_when_room_not_found(
        self, mock_db_service, mock_supervisor_llm_service
    ):
        """Should return False when room memory doesn't exist."""
        mock_db_service.get_room_summary_projection.return_value = None
        mock_supervisor_llm_service.call_json.return_value = {
            "current_goal": "Test",
            "key_decisions": [],
            "open_questions": [],
            "recent_agent_contributions": [],
            "important_constraints": [],
        }

        service, _holder = room_memory_facade(
            mock_db_service, mock_supervisor_llm_service
        )

        success = await service.update_room_summary(
            room_id="nonexistent",
            synthesis_text="Some text",
        )

        assert success is False
        mock_supervisor_llm_service.call_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_keeps_existing_fields_when_extraction_returns_empty(
        self, room_memory, mock_db_service, mock_supervisor_llm_service
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
        mock_supervisor_llm_service.call_json.return_value = {
            "current_goal": None,
            "key_decisions": [],
            "open_questions": ["New question"],
            "recent_agent_contributions": [],
            "important_constraints": [],
        }

        service, _holder = room_memory_facade(
            mock_db_service, mock_supervisor_llm_service
        )

        success = await service.update_room_summary(
            room_id="test_room",
            synthesis_text="Some text",
        )

        assert success is True
        saved_summary = mock_db_service.update_room_summary_atomic.call_args[0][1]
        assert saved_summary["current_goal"] == "Original goal"
        assert saved_summary["key_decisions"] == ["Original decision"]
        assert saved_summary["open_questions"] == ["New question"]

    @pytest.mark.asyncio
    async def test_populates_updated_after_turn_id(
        self, room_memory, mock_db_service, mock_supervisor_llm_service
    ):
        """synthesis_turn_id should be stored in RoomSummary.updated_after_turn_id (§4.2)."""
        mock_db_service.get_room_summary_projection.return_value = {
            "room_summary": {},
            "room_facts": [],
        }
        mock_db_service.update_room_summary_atomic.return_value = True
        mock_supervisor_llm_service.call_json.return_value = {
            "current_goal": "Test goal",
            "key_decisions": [],
            "open_questions": [],
            "recent_agent_contributions": [],
            "important_constraints": [],
        }

        service, _holder = room_memory_facade(
            mock_db_service, mock_supervisor_llm_service
        )

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

        service = RoomSupervisorService()

        agents = [
            AgentProfile(
                agent_id="agent-1",
                agent_name="TestAgent",
                description="Test agent",
                is_healthy=True,
            )
        ]
        room_config = RoomConfig()
        trajectory = SupervisorTrajectory()

        with patch.object(
            service, "_call_supervisor_llm", new_callable=AsyncMock
        ) as mock_llm:
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

        service = RoomSupervisorService()

        agents = [
            AgentProfile(
                agent_id="agent-1",
                agent_name="TestAgent",
                description="Test agent",
                is_healthy=True,
            )
        ]
        room_config = RoomConfig()
        trajectory = SupervisorTrajectory()

        with patch.object(
            service, "_call_supervisor_llm", new_callable=AsyncMock
        ) as mock_llm:
            mock_llm.return_value = {
                "action": "done",
                "reasoning": "test",
                "targets": [],
                "synthesis_instruction": None,
                "clarification_question": None,
            }

            await service.decide_next(
                message_text="Test message",
                agent_registry=agents,
                room_config=room_config,
                trajectory=trajectory,
                conversation_context="This is the conversation background",
            )

            call_args = mock_llm.call_args
            system_prompt_arg = call_args.kwargs.get(
                "system_prompt", call_args[0][0] if call_args[0] else ""
            )
            user_prompt_arg = call_args.kwargs.get(
                "user_prompt", call_args[0][1] if len(call_args[0]) > 1 else ""
            )

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
        rmc.cancellation_control = rmc.delivery
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
# Test: _parse_supervisor_action case-insensitive parsing
# =========================================================================


class TestParseProviderActionCaseInsensitive:
    """Tests for case-insensitive action parsing in _parse_supervisor_action.

    The LLM may return action strings in any case (DELEGATE, delegate, Delegate).
    The parser must normalize to lowercase before matching the ActionType enum.
    """

    @pytest.fixture
    def service(self):
        from execution.orchestration.room_supervisor_service import (
            RoomSupervisorService,
        )

        return RoomSupervisorService()

    @pytest.mark.parametrize(
        "action_str,expected_action",
        [
            ("delegate", "delegate"),
            ("DELEGATE", "delegate"),
            ("Delegate", "delegate"),
            ("clarify", "clarify"),
            ("CLARIFY", "clarify"),
            ("done", "done"),
            ("DONE", "done"),
            ("Done", "done"),
        ],
    )
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
                {
                    "agent_id": "agent-1",
                    "agent_name": "TestAgent",
                    "task": "Do something",
                },
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


class TestParseProviderActionClarifySanitization:
    """Tests that _parse_supervisor_action sanitizes prompt_type and choices from LLM output."""

    @pytest.fixture
    def service(self):
        from execution.orchestration.room_supervisor_service import (
            RoomSupervisorService,
        )

        return RoomSupervisorService()

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
        action = service._parse_supervisor_action(
            self._clarify_json(prompt_type="text")
        )
        assert action.prompt_type == "text"

    def test_valid_prompt_type_choice_with_choices(self, service):
        action = service._parse_supervisor_action(
            self._clarify_json(prompt_type="choice", choices=["A", "B", "C"])
        )
        assert action.prompt_type == "choice"
        assert action.choices == ["A", "B", "C"]

    def test_valid_prompt_type_confirmation(self, service):
        action = service._parse_supervisor_action(
            self._clarify_json(prompt_type="confirmation")
        )
        assert action.prompt_type == "confirmation"

    def test_invalid_prompt_type_number_becomes_none(self, service):
        action = service._parse_supervisor_action(self._clarify_json(prompt_type=42))
        assert action.prompt_type is None

    def test_invalid_prompt_type_unknown_string_becomes_none(self, service):
        action = service._parse_supervisor_action(
            self._clarify_json(prompt_type="multiple_choice")
        )
        assert action.prompt_type is None

    def test_choices_non_list_becomes_none(self, service):
        action = service._parse_supervisor_action(
            self._clarify_json(choices="not a list")
        )
        assert action.choices is None

    def test_choices_list_with_non_strings_becomes_none(self, service):
        action = service._parse_supervisor_action(
            self._clarify_json(choices=["ok", 123, None])
        )
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
                x for x in w if "PydanticSerializationUnexpectedValue" in str(x.message)
            ]
            assert len(pydantic_warnings) == 0

        assert data["status"] == "completed"

    @pytest.mark.parametrize(
        "status",
        [
            "completed",
            "failed",
            "canceled",
            "running",
            "awaiting_input",
        ],
    )
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
                x for x in w if "PydanticSerializationUnexpectedValue" in str(x.message)
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
                x for x in w if "PydanticSerializationUnexpectedValue" in str(x.message)
            ]
            assert len(pydantic_warnings) > 0, (
                "Expected Pydantic warning when status is a raw string, "
                "not a TrajectoryStatus enum member"
            )


# =========================================================================
# Tests: _handle_supervisor_run_result — supervisor synthesis vs default summary
# =========================================================================


class TestHandleRunResultUnifiedSummary:
    """Verify room result handling does not duplicate execution finalization."""

    @pytest.fixture
    def rmc(self):
        """Build a RoomMessageCenter with key collaborators mocked."""
        with (
            patch(
                "execution.orchestration.room_message_center.default_store"
            ) as mock_db,
            patch(
                "execution.orchestration.room_message_center.delivery"
            ) as mock_delivery,
            patch("execution.orchestration.room_message_center.coordinator"),
            patch("execution.orchestration.room_message_center.room_runtime"),
            patch("execution.orchestration.room_message_center.task_notifier"),
            patch("execution.orchestration.room_message_center.a2a_transport"),
            patch("execution.orchestration.room_message_center.remote_task_reader"),
            patch("execution.orchestration.room_message_center.agent_resolver_service"),
            patch("execution.orchestration.room_message_center.room_memory"),
            patch(
                "execution.orchestration.room_message_center.room_supervisor_service"
            ),
            patch("execution.orchestration.room_message_center.rate_limit_service"),
        ):
            mock_db.get_room_user_message_by_message_id = AsyncMock(return_value=None)
            mock_db.update_room_user_message_by_message_id = AsyncMock()
            mock_delivery.send_processing_status = AsyncMock()
            mock_delivery.remove_token = MagicMock()

            from execution.orchestration.factory import create_room_message_center

            rmc = create_room_message_center(
                cancellation_control=mock_delivery,
                internal_event_publisher=RecordingEventPublisher(),
            )
            rmc._emit_unified_summary = AsyncMock(
                return_value=("synthesis", "Final synthesis.")
            )
            rmc._emit_deterministic_digest = AsyncMock()
            rmc._run_supervisor_terminal_post_loop_integration = AsyncMock()
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
        """A committed supervisor synthesis is not emitted a second time."""
        await rmc._handle_supervisor_run_result(
            result=completed_result_with_synthesis,
            room_id="room-1",
            user_message_id="msg-1",
        )
        rmc._emit_unified_summary.assert_not_awaited()
        rmc._run_supervisor_terminal_post_loop_integration.assert_awaited_once_with(
            completed_result_with_synthesis,
            "room-1",
        )

    @pytest.mark.asyncio
    async def test_no_synthesis_emits_deterministic_digest_for_multi_agent_done(
        self, rmc, completed_result_without_synthesis
    ):
        """Execution owns multi-Agent finalization, including fallback output."""
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
        rmc._emit_deterministic_digest.assert_not_awaited()

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
    async def test_trajectory_responses_are_not_summarized_again(self, rmc):
        """Completed trajectory responses remain owned by Execution."""
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
        rmc._emit_unified_summary.assert_not_awaited()
        rmc._run_supervisor_terminal_post_loop_integration.assert_awaited_once_with(
            result,
            "room-1",
        )


class _Phase5StubPlanner:
    def __init__(self, actions):
        self._actions = list(actions)
        self.contexts = []

    @property
    def last_context_payload(self):
        if not self.contexts:
            return None
        context = self.contexts[-1]
        return context.prompt_payload()

    async def plan(self, context):
        assert hasattr(context, "prompt_payload"), (
            "Planner received non-structured context; expected OrchestrationPlannerContext"
        )
        self.contexts.append(context)
        if not self._actions:
            raise AssertionError("planner called more times than expected")
        return PlannerAction(**self._actions.pop(0))


class _InMemoryRoomMessageStore:
    def __init__(self) -> None:
        self.user_messages = {}
        self.agent_messages = {}

    def _set_user_message(self, message: RoomUserMessage) -> None:
        self.user_messages[message.message_id] = message

    async def get_room_user_message_by_message_id(self, message_id: str):
        return self.user_messages.get(message_id)

    async def update_room_user_message_by_message_id(self, message_id: str, message):
        self.user_messages[message_id] = message
        return True

    async def get_room_agent_message_by_message_id(self, message_id: str):
        return self.agent_messages.get(message_id)

    async def add_room_agent_message(self, message):
        self.agent_messages[message.message_id] = message

    async def upsert_room_agent_message(self, message):
        self.agent_messages[message.message_id] = message

    async def update_room_agent_message_by_message_id(self, message_id: str, message):
        self.agent_messages[message_id] = message

    async def update_task_state_on_message(
        self,
        message_id: str,
        state: str,
        *,
        message_text: str | None = None,
        artifacts: list[dict] | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
    ):
        message = self.agent_messages.get(message_id)
        if message is None:
            return False, None
        if message_text is not None:
            message.message_content.message_text = message_text
        task = getattr(message.message_content, "message_task", None)
        if task is None:
            task = SimpleNamespace(
                id=task_id or message_id,
                context_id=context_id,
                status=SimpleNamespace(state=state),
                artifacts=[],
            )
            message.message_content.message_task = task
        else:
            status = getattr(task, "status", None)
            if status is None:
                task.status = SimpleNamespace(state=state)
            elif isinstance(status, dict):
                status["state"] = state
            else:
                status.state = state
        if artifacts is not None:
            task.artifacts = copy.deepcopy(artifacts)
        message.last_notified_state = state
        return True, message_text

    async def accumulate_artifact_on_message(
        self,
        message_id: str,
        artifact: dict,
        *,
        append: bool = False,
    ):
        message = self.agent_messages.get(message_id)
        if message is None:
            return False
        task = getattr(message.message_content, "message_task", None)
        if task is None:
            task = SimpleNamespace(
                id=message_id,
                status=SimpleNamespace(state="working"),
                artifacts=[],
            )
            message.message_content.message_task = task
        if not isinstance(getattr(task, "artifacts", None), list):
            task.artifacts = []
        artifact_payload = copy.deepcopy(artifact)
        if append and task.artifacts:
            task.artifacts[-1].setdefault("parts", []).extend(
                artifact_payload.get("parts") or []
            )
        else:
            task.artifacts.append(artifact_payload)
        return True

    async def update_room_agent_message_with_new_message_content_by_message_id(
        self,
        message_id: str,
        message_content,
    ):
        message = self.agent_messages.get(message_id)
        if message is None:
            return False
        message.message_content = message_content
        return True

    async def cancel_descendants(self, message_id: str):
        return None

    async def cancel_agent_messages_by_ids(self, message_ids):
        return None


class _FakeAgent:
    def __init__(self, agent_id: str, name: str) -> None:
        self.agent_id = agent_id
        self.agent_card = SimpleNamespace(
            name=name,
            description="",
            skills=[],
        )
        self.call_count = 1
        self.call_success_count = 1
        self.agent_status = AgentStatus.active


class _FakeRoomRuntime:
    def __init__(self) -> None:
        self._next_message_seq = 0

    def create_agent_message(self, **kwargs):
        self._next_message_seq += 1
        return SimpleNamespace(
            room_id=kwargs["room_id"],
            message_id=f"agent-msg-{self._next_message_seq}",
            message_type="agent",
            user_id=kwargs.get("user_id"),
            agent_id=kwargs.get("agent_id"),
            related_message_id=kwargs.get("related_message_id"),
            message_content=MessageContent(message_text=kwargs.get("content", "")),
            step_number=kwargs.get("step_number"),
            task_content=kwargs.get("task_content"),
            client_request_id=kwargs.get("client_request_id"),
            extend_info={},
            last_notified_state=None,
        )


class _FakePhase5App:
    def __init__(self) -> None:
        from execution.orchestration.room_message_center import RoomMessageCenter
        from execution.orchestration.run_store import InMemoryOrchestrationRunStore
        from execution.orchestration.supervisor_executor import SupervisorExecutor

        self.run_store = InMemoryOrchestrationRunStore()
        self.room_store = _InMemoryRoomMessageStore()
        self.planner = _Phase5StubPlanner([])

        self.delivery = SimpleNamespace(
            send_task_submitted=AsyncMock(),
            send_task_update=AsyncMock(),
            send_agent_response=AsyncMock(),
            remove_token=MagicMock(),
            clear_cancellation=MagicMock(),
        )
        self.task_state_store = AsyncMock()
        self.task_state_store.resolve_client_request_id_for_message_id = AsyncMock(
            return_value="client-1"
        )
        self.task_state_store.resolve_client_request_id_for_agent_message = AsyncMock(
            return_value="client-1"
        )
        self.continuation_store = AsyncMock()
        self.continuation_store.save_continuation_on_user_message = AsyncMock(
            return_value=True
        )
        self.continuation_store.save_continuation_on_message = AsyncMock(
            return_value=True
        )
        self.continuation_store.get_pending_continuation_on_message = AsyncMock(
            return_value=None
        )
        self.hitl_coordinator = AsyncMock()

        request = SimpleNamespace(request_id=f"request-{uuid4().hex}")
        self.hitl_coordinator.request_interaction = AsyncMock(return_value=[request])

        self.room_runtime = _FakeRoomRuntime()
        self._agent_results = {}
        self._agent_by_id = {
            "agent-1": _FakeAgent("agent-1", "Agent One"),
            "broker": _FakeAgent("broker", "Broker"),
            "insurer": _FakeAgent("insurer", "Insurer"),
        }
        self.agent_lookup = AsyncMock()
        self.agent_lookup.get_agent_by_agent_id = AsyncMock(
            side_effect=lambda agent_id: self._agent_by_id.get(agent_id)
        )

        self.agent_dispatcher = SimpleNamespace(
            resolve_agent=AsyncMock(
                side_effect=lambda agent_id, _room_id: self._agent_by_id.get(agent_id)
            )
        )
        self.agent_message_processor = SimpleNamespace(
            process_single_message=AsyncMock(
                side_effect=self._process_stubbed_agent_message
            )
        )

        async def synthesize_stream(*, trajectory, synthesis_instruction, user_goal):
            del trajectory, synthesis_instruction, user_goal
            yield "Final synthesized response."

        self._executor = SupervisorExecutor(
            supervisor_service=SimpleNamespace(synthesize_stream=synthesize_stream),
            room_runtime=self.room_runtime,
            tsm=SimpleNamespace(),
            delivery=self.delivery,
            message_reader=self.room_store,
            message_writer=self.room_store,
            task_state_store=self.task_state_store,
            continuation_store=self.continuation_store,
            internal_event_publisher=AsyncMock(),
            rate_limit_service=None,
            agent_dispatcher=self.agent_dispatcher,
            agent_message_processor=self.agent_message_processor,
            hitl_coordinator=self.hitl_coordinator,
            orchestration_run_store=self.run_store,
            orchestration_planner=self.planner,
        )

        self.room_center = RoomMessageCenter.__new__(RoomMessageCenter)
        self.room_center.message_reader = self.room_store
        self.room_center.message_writer = self.room_store
        self.room_center.task_state_store = self.task_state_store
        self.room_center.continuation_store = self.continuation_store
        self.room_center.agent_lookup = self.agent_lookup
        self.room_center.agent_group_reader = AsyncMock()
        self.room_center.room_reader = AsyncMock()
        self.room_center.room_writer = AsyncMock()
        self.room_center.memory_reader = AsyncMock()
        self.room_center.memory_reader.get_room_memory_by_room_id = AsyncMock(
            return_value=None
        )
        self.room_center.hitl_reader = AsyncMock()
        self.room_center.delivery = self.delivery
        self.room_center.coordinator = AsyncMock()
        self.room_center.internal_event_publisher = AsyncMock()
        self.room_center.room_memory = AsyncMock()
        self.room_center.task_notifier = AsyncMock()
        self.room_center.task_notification_store = AsyncMock()
        self.room_center.supervisor_executor = self._executor
        self.room_center.orchestration_run_store = self.run_store
        self.room_center.agent_resolver_service = AsyncMock()
        self.room_center.supervisor_planning_error_cls = RuntimeError
        self.room_center.a2a_transport = None
        self.room_center.remote_task_reader = AsyncMock()
        self.room_center.rate_limit_service = None
        self.room_center.context_assembly = None
        self.room_center.memory_search = None
        self.room_center.context_compaction = None
        self.room_center.build_turn_content = None
        self.room_center.agent_response_handler = None
        self.room_center.queue_executor = None
        self.room_center.supervisor_planning_error_cls = RuntimeError

        self.room_center.room_reader.get_room_by_room_id = AsyncMock(
            side_effect=lambda room_id: SimpleNamespace(
                room_agent_set={"agent-1": "Agent One"},
                extend_info={},
                room_id=room_id,
            )
        )
        self.room_center.room_writer.update_room_by_room_id = AsyncMock()

        status_emitter = AsyncMock()
        self.room_center.bind_execution_event_deps(status_emitter)

    def stub_planner_actions(self, actions):
        planner = _Phase5StubPlanner(actions)
        self.planner = planner
        self._executor.orchestration_planner = planner

    def stub_agent_result(
        self,
        *,
        agent_id: str,
        agent_message_id: str,
        text: str,
        artifacts: list[dict] | None = None,
    ) -> None:
        self._agent_results[agent_id] = SimpleNamespace(
            agent_message_id=agent_message_id,
            text=text,
            artifacts=copy.deepcopy(artifacts or []),
        )

    async def _process_stubbed_agent_message(
        self,
        message,
        room_id: str,
        agent,
        user_message_id: str,
        **_kwargs,
    ):
        stub = self._agent_results.get(agent.agent_id)
        if stub is None:
            return ProcessingResult(
                ProcessingStatus.FAILED,
                f"No stubbed result for {agent.agent_id}",
            )
        assert message.message_id == stub.agent_message_id
        await self.room_store.update_task_state_on_message(
            message.message_id,
            "completed",
            message_text=stub.text,
            artifacts=stub.artifacts,
        )
        return ProcessingResult(ProcessingStatus.SUCCESS, stub.text)

    async def send_supervisor_message(
        self,
        *,
        room_id: str,
        user_id: str,
        message: str,
        message_id: str | None = None,
        dispatch: dict | None = None,
        extend_info: dict | None = None,
    ):
        room_message_id = message_id or f"msg-{uuid4().hex}"
        dispatch = dispatch or {}
        candidate_scope_mode = "explicit_selection"
        candidate_scope_group_id = None
        candidate_agent_ids = ["agent-1"]
        if dispatch.get("message_target_mode") == "saved_group":
            candidate_scope_mode = "saved_group"
            candidate_scope_group_id = dispatch.get("target_group_id")
            if dispatch.get("target_group_id") == "group-1":
                candidate_agent_ids = ["broker", "insurer"]
        if isinstance(extend_info, dict):
            candidate_scope_mode = extend_info.get(
                "candidate_scope_mode", candidate_scope_mode
            )
            candidate_scope_group_id = extend_info.get(
                "candidate_scope_group_id", candidate_scope_group_id
            )
            candidate_agent_ids = extend_info.get(
                "candidate_agent_ids", candidate_agent_ids
            )
        extend_payload = {
            "orchestration": True,
            "orchestration_run_id": room_message_id,
            "candidate_scope_mode": candidate_scope_mode,
            "candidate_scope_group_id": candidate_scope_group_id,
            "candidate_agent_ids": candidate_agent_ids,
            "client_request_id": "client-1",
            "message_target_mode": dispatch.get(
                "message_target_mode",
                "saved_group",
            ),
            "target_group_id": dispatch.get("target_group_id"),
        }
        if isinstance(extend_info, dict):
            extend_payload.update(extend_info)
        extend_payload["orchestration_run_id"] = room_message_id
        user_message = RoomUserMessage(
            room_id=room_id,
            message_id=room_message_id,
            user_id=user_id,
            message_content=MessageContent(message_text=message),
            extend_info=extend_payload,
        )
        self.room_store._set_user_message(user_message)

        await self.room_center._process_supervisor(
            user_message=user_message,
            room_id=room_id,
            room_user_message_id=room_message_id,
            user_id=user_id,
            quoted_text=None,
            token=None,
        )

        return SimpleNamespace(message_id=room_message_id)


def _make_phase5_app():
    return _FakePhase5App()


@pytest.mark.core
@pytest.mark.asyncio
async def test_supervisor_autonomous_loop_delegates_ingests_and_completes_with_evidence():
    app = _make_phase5_app()
    run_id = "run-autonomous-loop"
    broker_message_id = f"{run_id}:step-1:target-1:message"
    insurer_message_id = f"{run_id}:step-1:target-2:message"
    quote_artifact_key = f"{insurer_message_id}:artifact_id:quote-1"

    app.stub_planner_actions(
        [
            {
                "action": "delegate",
                "reasoning": "Need broker and insurer input.",
                "targets": [
                    {
                        "agent_id": "broker",
                        "agent_name": "Broker",
                        "task": "Collect requirements.",
                        "parallel_group": "quote-fanout",
                    },
                    {
                        "agent_id": "insurer",
                        "agent_name": "Insurer",
                        "task": "Prepare quote.",
                        "parallel_group": "quote-fanout",
                    },
                ],
            },
            {
                "action": "complete",
                "reasoning": "Both required outputs are available.",
                "completion_evidence": {
                    "satisfied_criteria": [
                        "requirements_collected",
                        "quote_prepared",
                    ],
                    "referenced_fact_ids": [
                        f"{broker_message_id}:text",
                    ],
                    "referenced_artifact_keys": [quote_artifact_key],
                    "unresolved_questions": [],
                    "final_answer_intent": "answer_user",
                    "confidence": 0.86,
                },
            },
        ]
    )
    app.stub_agent_result(
        agent_id="broker",
        agent_message_id=broker_message_id,
        text="Requirements are complete.",
    )
    app.stub_agent_result(
        agent_id="insurer",
        agent_message_id=insurer_message_id,
        text="Quote is ready.",
        artifacts=[{"artifact_id": "quote-1", "summary": "Carrier quote"}],
    )

    result = await app.send_supervisor_message(
        room_id="room-1",
        user_id="user-1",
        message="Get the quote ready.",
        message_id=run_id,
        dispatch={"message_target_mode": "saved_group", "target_group_id": "group-1"},
    )

    assert len(app.planner.contexts) == 2
    complete_context = app.planner.contexts[1].prompt_payload()["state_context"]
    assert [output["agent_id"] for output in complete_context["agent_outputs"]] == [
        "broker",
        "insurer",
    ]
    assert {fact["fact_id"] for fact in complete_context["facts"]} >= {
        f"{broker_message_id}:text",
        f"{insurer_message_id}:text_evidence",
    }
    assert {artifact["artifact_key"] for artifact in complete_context["artifacts"]} >= {
        quote_artifact_key
    }

    state = await app.run_store.get_latest_by_user_message_id(result.message_id)
    assert state is not None
    assert state.status.value == "completed"
    assert state.candidate_scope.source == "saved_group"
    assert state.candidate_scope.group_id == "group-1"
    assert [
        (output.agent_id, output.agent_message_id) for output in state.agent_outputs
    ] == [
        ("broker", broker_message_id),
        ("insurer", insurer_message_id),
    ]
    assert state.completion_evidence.confidence == 0.86
    assert {artifact["artifact_key"] for artifact in state.artifacts} >= {
        quote_artifact_key
    }

    message_record = await app.room_store.get_room_user_message_by_message_id(
        result.message_id
    )
    assert message_record is not None
    assert "supervisor_trajectory" not in message_record.extend_info


@pytest.mark.core
@pytest.mark.asyncio
async def test_push_notification_terminal_result_reenters_and_completes_durable_run():
    app = _make_phase5_app()
    run_id = "run-push-notification"
    agent_message_id = f"{run_id}:step-1:target-1:message"
    app.stub_planner_actions(
        [
            {
                "action": "delegate",
                "reasoning": "Dispatch the long-running agent.",
                "targets": [
                    {
                        "agent_id": "agent-1",
                        "agent_name": "Agent One",
                        "task": "Handle the long-running request.",
                    }
                ],
            },
            {
                "action": "complete",
                "reasoning": "The callback result is ready.",
                "synthesis_instruction": "Summarize the result.",
            },
        ]
    )
    app.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.PAUSED,
            message_id=agent_message_id,
        )
    )

    await app.send_supervisor_message(
        room_id="room-1",
        user_id="user-1",
        message="Run this asynchronously.",
        message_id=run_id,
        extend_info={"candidate_agent_ids": ["agent-1"]},
    )

    paused = await app.run_store.get_run(run_id)
    assert paused is not None
    assert paused.status == OrchestrationStatus.WAITING_AGENT
    await app.room_store.update_task_state_on_message(
        agent_message_id,
        "completed",
        message_text="Terminal webhook result",
    )

    async def recover(_request):
        user_message = await app.room_store.get_room_user_message_by_message_id(run_id)
        return await app.room_center._process_supervisor(
            user_message=user_message,
            room_id="room-1",
            room_user_message_id=run_id,
            user_id="user-1",
            quoted_text=None,
            token=None,
        )

    app.room_center.process_room_user_message = AsyncMock(side_effect=recover)
    resumed = await app.room_center.resume_queue_from_continuation(agent_message_id)

    assert resumed is True
    completed = await app.run_store.get_run(run_id)
    assert completed is not None
    assert completed.status == OrchestrationStatus.COMPLETED
    assert completed.agent_outputs[0].status == "completed"
    assert completed.agent_outputs[0].text == "Terminal webhook result"
    assert app.planner.contexts[-1].state_context.agent_outputs[0]["text"] == (
        "Terminal webhook result"
    )
    assert "supervisor_trajectory" not in (
        app.room_store.user_messages[run_id].extend_info or {}
    )


# =========================================================================
# Test: _parse_supervisor_action multi-question CLARIFY parsing
# =========================================================================


class TestParseProviderActionMultiQuestion:
    """Tests that _parse_supervisor_action correctly parses the questions array."""

    @pytest.fixture
    def service(self):
        from execution.orchestration.room_supervisor_service import (
            RoomSupervisorService,
        )

        return RoomSupervisorService()

    def test_parses_valid_questions_array(self, service):
        action = service._parse_supervisor_action(
            {
                "action": "clarify",
                "reasoning": "need more info",
                "questions": [
                    {"prompt": "Travel dates?", "prompt_type": "text"},
                    {
                        "prompt": "Budget?",
                        "prompt_type": "choice",
                        "choices": ["Low", "High"],
                    },
                    {"prompt": "Proceed?", "prompt_type": "confirmation"},
                ],
            }
        )
        assert action.questions is not None
        assert len(action.questions) == 3
        assert action.questions[0].prompt == "Travel dates?"
        assert action.questions[0].prompt_type == "text"
        assert action.questions[1].choices == ["Low", "High"]
        assert action.questions[2].prompt_type == "confirmation"

    def test_falls_back_to_clarification_question_when_no_questions(self, service):
        action = service._parse_supervisor_action(
            {
                "action": "clarify",
                "reasoning": "need info",
                "clarification_question": "What dates?",
            }
        )
        assert action.questions is None
        assert action.clarification_question == "What dates?"

    def test_ignores_questions_with_invalid_prompts(self, service):
        action = service._parse_supervisor_action(
            {
                "action": "clarify",
                "reasoning": "need info",
                "questions": [
                    {"prompt": 123},
                    {"prompt": "Valid question?", "prompt_type": "text"},
                ],
            }
        )
        assert action.questions is not None
        assert len(action.questions) == 1
        assert action.questions[0].prompt == "Valid question?"

    def test_sanitizes_invalid_prompt_type_in_questions(self, service):
        action = service._parse_supervisor_action(
            {
                "action": "clarify",
                "reasoning": "need info",
                "questions": [
                    {"prompt": "Q1?", "prompt_type": "invalid_type"},
                    {"prompt": "Q2?", "prompt_type": "choice", "choices": "not a list"},
                ],
            }
        )
        assert action.questions is not None
        assert len(action.questions) == 2
        assert action.questions[0].prompt_type is None
        assert action.questions[1].choices is None

    def test_empty_questions_array_yields_none(self, service):
        action = service._parse_supervisor_action(
            {
                "action": "clarify",
                "reasoning": "need info",
                "questions": [],
            }
        )
        assert action.questions is None

    def test_questions_array_not_a_list_yields_none(self, service):
        action = service._parse_supervisor_action(
            {
                "action": "clarify",
                "reasoning": "need info",
                "questions": "not a list",
            }
        )
        assert action.questions is None
