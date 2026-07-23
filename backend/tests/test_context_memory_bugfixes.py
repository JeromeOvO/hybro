"""
Unit tests for context & memory bug fixes and new functionality.

Covers:
- CompactionSweep: attribute names, active-room skip, semaphore
- extract_turn_notes_llm: LLM path and heuristic fallback
- Memory search result hydration: _hydrate_results_from_storage
- was_successful propagation through add_turn_to_history

See CONTEXT_MEMORY_SYSTEM_DESIGN.md for design specification.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from common.types import MessageRole
from common.utils.context_utils import (
    add_turn_to_history,
    estimate_tokens,
)
from context_memory import search
from context_memory.models import SearchRankingRecord
from models.compaction import CompactionResult
from models.memory import (
    ContentType,
    ConversationTurn,
    MemoryContent,
    RoomMemory,
    TurnRepresentation,
    TurnRole,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_compaction_config():
    with patch("models.context_config.settings") as mock:
        mock.compaction_enabled = True
        mock.compaction_max_full_turns = 20
        mock.compaction_max_total_tokens = 80000
        mock.compaction_preserve_recent = 10
        mock.compaction_content_ttl_days = 0
        mock.memory_search_enabled = True
        mock.memory_search_temporal_decay_enabled = True
        mock.memory_search_half_life_days = 30
        mock.memory_search_max_results = 10
        mock.memory_search_max_snippet_chars = 500
        yield mock


def _make_turn(content="Test", role=TurnRole.USER, **kwargs) -> ConversationTurn:
    defaults = dict(
        turn_id=str(uuid4()),
        role=role,
        content=content,
        content_type=ContentType.TEXT,
        representation=TurnRepresentation.FULL,
        estimated_tokens_full=estimate_tokens(content),
        estimated_tokens_compact=20,
        timestamp=datetime(2026, 2, 20),
    )
    defaults.update(kwargs)
    return ConversationTurn(**defaults)


# =============================================================================
# CompactionSweep Tests
# =============================================================================


class TestCompactionSweep:
    """Tests for the CompactionSweep background job."""

    @pytest.mark.asyncio
    async def test_sweep_uses_correct_attribute_names(self, mock_compaction_config):
        """Verify sweep uses repository room ids and compacted_count."""
        from jobs.compaction_sweep import CompactionSweep, CompactionSweepDeps

        sweep = CompactionSweep(interval_minutes=60)

        mock_result = CompactionResult(
            room_id="room_1",
            compacted_count=3,
            tokens_saved=500,
        )
        mock_compaction_svc = AsyncMock()
        mock_compaction_svc.compact_if_needed = AsyncMock(return_value=mock_result)

        sweep.set_sweep_deps(
            CompactionSweepDeps(
                list_room_ids_with_memory=AsyncMock(return_value=["room_1"]),
                get_room_ids_with_non_terminal_runs=AsyncMock(return_value=[]),
                context_compaction=mock_compaction_svc,
            )
        )

        stats = await sweep.sweep()

        assert stats["scanned"] == 1
        assert stats["compacted"] == 1
        mock_compaction_svc.compact_if_needed.assert_awaited_once_with("room_1")

    @pytest.mark.asyncio
    async def test_sweep_skips_rooms_with_active_processing(
        self, mock_compaction_config
    ):
        """Rooms with non-terminal runs should be skipped."""
        from jobs.compaction_sweep import CompactionSweep, CompactionSweepDeps

        sweep = CompactionSweep(interval_minutes=60)

        mock_result = CompactionResult(
            room_id="idle_room",
            compacted_count=1,
            tokens_saved=100,
        )
        mock_compaction_svc = AsyncMock()
        mock_compaction_svc.compact_if_needed = AsyncMock(return_value=mock_result)

        sweep.set_sweep_deps(
            CompactionSweepDeps(
                list_room_ids_with_memory=AsyncMock(
                    return_value=["active_room", "idle_room"]
                ),
                get_room_ids_with_non_terminal_runs=AsyncMock(
                    return_value=["active_room"]
                ),
                context_compaction=mock_compaction_svc,
            )
        )

        stats = await sweep.sweep()

        assert stats["scanned"] == 2
        assert stats["skipped"] == 1
        mock_compaction_svc.compact_if_needed.assert_awaited_once_with("idle_room")


# =============================================================================
# extract_turn_notes_llm Tests
# =============================================================================


class TestExtractTurnNotesLLM:
    """Tests for LLM-based turn note extraction."""

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_content(self):
        from common.utils.context_utils import extract_turn_notes_llm

        result = await extract_turn_notes_llm("")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_short_content(self):
        from common.utils.context_utils import extract_turn_notes_llm

        result = await extract_turn_notes_llm("hi")
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_llm_and_parses_result(self):
        from common.dto import LLMStructuredResponse
        from common.utils import context_utils

        long_content = "Discuss the deployment of React application " * 50
        mock_response = {
            "keywords": ["React", "deployment", "application"],
            "entities": ["React"],
            "tags": ["deployment"],
            "one_liner": "Discussion about deploying React app",
        }

        mock_gateway = MagicMock()
        mock_gateway.generate_structured = AsyncMock(
            return_value=LLMStructuredResponse(
                data=mock_response,
                model="gpt-4o-mini",
            )
        )

        result = await context_utils.extract_turn_notes_llm(
            long_content, provider=mock_gateway
        )

        assert result is not None
        assert "keywords" in result
        assert len(result["keywords"]) <= 10
        mock_gateway.generate_structured.assert_awaited_once()
        assert (
            mock_gateway.generate_structured.await_args.kwargs["model"]
            == "context_memory_legacy_json_model"
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_heuristic_on_llm_failure(self):
        from common.utils import context_utils

        long_content = "Discuss deployment of the application system " * 50

        mock_gateway = MagicMock()
        mock_gateway.generate_structured = AsyncMock(side_effect=Exception("LLM error"))

        result = await context_utils.extract_turn_notes_llm(
            long_content, provider=mock_gateway
        )

        assert result is not None
        assert "keywords" in result


# =============================================================================
# Memory Search Hydration Tests
# =============================================================================


class TestMemorySearchHydration:
    """Tests for keyword ranking records used by two-stage hydration."""

    def test_keyword_ranking_record_uses_keyword_relevance_fields(
        self, mock_compaction_config
    ):
        results = [
            SearchRankingRecord(
                turn_id="turn_1",
                content="",
                room_id="room_1",
                keyword_score=0.95,
                timestamp=datetime(2026, 2, 20),
            ),
        ]

        ranked = search.rank_keyword_results(
            results,
            temporal_decay_enabled=False,
            half_life_days=30,
        )

        assert ranked[0].keyword_score == 1.0
        assert ranked[0].relevance_score == 1.0
        assert ranked[0].temporal_decay_factor == 1.0

    def test_keyword_ranking_preserves_existing_content(self, mock_compaction_config):
        results = [
            SearchRankingRecord(
                turn_id="turn_1",
                content="Already has content",
                room_id="room_1",
                keyword_score=0.95,
                timestamp=datetime(2026, 2, 20),
                metadata={"content_preview": "Already has content"},
            ),
        ]

        ranked = search.rank_keyword_results(
            results,
            temporal_decay_enabled=False,
            half_life_days=30,
        )

        assert ranked[0].content == "Already has content"


# =============================================================================
# was_successful Propagation Tests
# =============================================================================


class TestWasSuccessfulPropagation:
    """Tests that was_successful is correctly stored on ConversationTurn."""

    def test_add_turn_to_history_preserves_was_successful_true(self):
        mc = MemoryContent()
        result = add_turn_to_history(
            mc,
            role=MessageRole.AGENT,
            content="Agent completed the task successfully.",
            agent_id="agent_1",
            agent_name="TestAgent",
            was_successful=True,
        )
        assert len(result.conversation_history) == 1
        assert result.conversation_history[0].was_successful is True

    def test_add_turn_to_history_preserves_was_successful_false(self):
        mc = MemoryContent()
        result = add_turn_to_history(
            mc,
            role=MessageRole.AGENT,
            content="Agent failed to complete the task.",
            agent_id="agent_1",
            agent_name="TestAgent",
            was_successful=False,
        )
        assert len(result.conversation_history) == 1
        assert result.conversation_history[0].was_successful is False

    def test_add_turn_to_history_defaults_was_successful_to_none(self):
        mc = MemoryContent()
        result = add_turn_to_history(
            mc,
            role=MessageRole.USER,
            content="User message",
        )
        assert len(result.conversation_history) == 1
        assert result.conversation_history[0].was_successful is None


# =============================================================================
# Legacy Turn Token Fallback Tests
# =============================================================================


class TestLegacyTokenFallback:
    """Tests that estimated_tokens_full=0 falls back to estimate_tokens()."""

    def test_select_turns_within_budget_handles_zero_tokens(
        self, mock_compaction_config
    ):
        from context_memory.legacy_assembly import select_legacy_turns_within_budget

        mock_compaction_config.context_model_window = 32000
        mock_compaction_config.context_system_prompt_tokens = 2000
        mock_compaction_config.context_tool_schema_tokens = 3000
        mock_compaction_config.context_response_reserve_tokens = 4000
        mock_compaction_config.context_room_pct = 0.15
        mock_compaction_config.context_history_pct = 0.60
        mock_compaction_config.context_task_pct = 0.25

        legacy_turn = _make_turn(
            content="A " * 500,
            estimated_tokens_full=0,  # Legacy turn with no token estimate
        )
        recent_turn = _make_turn(content="Recent message")

        selected, truncated = select_legacy_turns_within_budget(
            turns=[legacy_turn, recent_turn],
            budget_tokens=50,
        )

        # Legacy turn with 0 estimated tokens should be correctly measured
        # at ~250 tokens (1000 chars / 4), exceeding the 50-token budget.
        # The selector should truncate the legacy turn and keep only the recent one.
        assert truncated == 1
        assert len(selected) == 1
        assert selected[0].content == "Recent message"


# =============================================================================
# Integration: Search → Context Pipeline Test
# =============================================================================


class TestSearchToContextIntegration:
    """Verify memory search results flow through to supervisor context output."""

    def test_search_results_appear_in_supervisor_context(self, mock_compaction_config):
        from context_memory.compat.context_assembly import ContextAssemblyService
        from models.search import MemorySearchResult, MemorySourceType

        mock_compaction_config.context_model_window = 32000
        mock_compaction_config.context_system_prompt_tokens = 2000
        mock_compaction_config.context_tool_schema_tokens = 3000
        mock_compaction_config.context_response_reserve_tokens = 4000
        mock_compaction_config.context_room_pct = 0.15
        mock_compaction_config.context_history_pct = 0.60
        mock_compaction_config.context_task_pct = 0.25

        class Facade:
            def assemble_supervisor_context_from_memory(
                self, room_memory_doc, current_task, **kwargs
            ):
                from context_memory.assembly import (
                    assemble_supervisor_context_from_memory,
                )

                return assemble_supervisor_context_from_memory(
                    room_memory_doc,
                    current_task,
                    agent_registry=kwargs.get("agent_registry"),
                    max_turns=kwargs.get("max_turns", 5),
                    memory_search_results=kwargs.get("memory_search_results"),
                )

        service = ContextAssemblyService()
        service.bind_facade(Facade())

        search_results = [
            MemorySearchResult(
                turn_id="turn_abc",
                content="User asked about deploying the React frontend",
                content_preview="User asked about deploying the React frontend",
                role=MessageRole.USER,
                room_id="room_1",
                source_type=MemorySourceType.TURN,
                keyword_score=0.92,
                relevance_score=0.92,
                timestamp=datetime(2026, 2, 20),
            ),
            MemorySearchResult(
                turn_id="turn_def",
                content="",
                content_preview=None,
                role=MessageRole.AGENT,
                agent_name="DevAgent",
                room_id="room_1",
                source_type=MemorySourceType.TURN,
                keyword_score=0.85,
                relevance_score=0.85,
                timestamp=datetime(2026, 2, 20),
            ),
        ]

        room_memory = RoomMemory(
            room_id="room_1",
            memory_content=MemoryContent(
                conversation_history=[
                    _make_turn(content="Hello"),
                ],
            ),
        )

        result = service.build_supervisor_context(
            room_memory=room_memory,
            current_task="Deploy the app",
            memory_search_results=search_results,
        )

        assert "deploying the React frontend" in result.context
        assert "[Relevant Memory]" in result.context
        # Empty-content result should NOT appear (no preview)
        assert "DevAgent" not in result.context or "" not in result.context
