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

from common.utils.context_utils import (
    LLM_TURN_NOTES_THRESHOLD,
    add_turn_to_history,
    extract_turn_notes,
    estimate_tokens,
)
from models.compaction import CompactionResult
from models.memory import (
    ConversationTurn,
    ContentType,
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
        mock.memory_search_vector_weight = 0.7
        mock.memory_search_keyword_weight = 0.3
        mock.memory_search_temporal_decay_enabled = True
        mock.memory_search_half_life_days = 30
        mock.memory_search_mmr_lambda = 0.7
        mock.memory_search_max_results = 10
        mock.memory_search_max_snippet_chars = 500
        mock.memory_search_index_name = "room-memory"
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
        """Verify sweep accesses room_memories_collection and compacted_count."""
        from jobs.compaction_sweep import CompactionSweep

        sweep = CompactionSweep(interval_minutes=60)

        mock_result = CompactionResult(
            room_id="room_1",
            compacted_count=3,
            tokens_saved=500,
        )
        mock_compaction_svc = AsyncMock()
        mock_compaction_svc.compact_if_needed = AsyncMock(return_value=mock_result)

        mock_rooms_coll = MagicMock()
        mock_rooms_cursor = AsyncMock()
        mock_rooms_cursor.__aiter__ = AsyncMock(return_value=iter([]))
        mock_rooms_coll.find.return_value = mock_rooms_cursor

        async def _room_docs():
            yield {"room_id": "room_1"}

        mock_mem_coll = MagicMock()
        mock_mem_coll.find.return_value = _room_docs()

        mock_mongodb = MagicMock()
        mock_mongodb.room_memories_collection = mock_mem_coll
        mock_mongodb.rooms_collection = mock_rooms_coll

        with (
            patch("database.mongodb.mongodb", mock_mongodb),
            patch("services.compaction_service.compaction_service", mock_compaction_svc),
        ):
            stats = await sweep.sweep()

        assert stats["scanned"] == 1
        assert stats["compacted"] == 1
        mock_compaction_svc.compact_if_needed.assert_awaited_once_with("room_1")

    @pytest.mark.asyncio
    async def test_sweep_skips_rooms_with_active_processing(self, mock_compaction_config):
        """Rooms with processing_message_id set should be skipped."""
        from jobs.compaction_sweep import CompactionSweep

        sweep = CompactionSweep(interval_minutes=60)

        mock_compaction_svc = AsyncMock()
        mock_compaction_svc.compact_if_needed = AsyncMock(return_value=None)

        async def _active_rooms():
            yield {"room_id": "active_room"}

        mock_rooms_coll = MagicMock()
        mock_rooms_coll.find.return_value = _active_rooms()

        async def _room_mem_docs():
            yield {"room_id": "active_room"}
            yield {"room_id": "idle_room"}

        mock_mem_coll = MagicMock()
        mock_mem_coll.find.return_value = _room_mem_docs()

        mock_mongodb = MagicMock()
        mock_mongodb.room_memories_collection = mock_mem_coll
        mock_mongodb.rooms_collection = mock_rooms_coll

        with (
            patch("database.mongodb.mongodb", mock_mongodb),
            patch("services.compaction_service.compaction_service", mock_compaction_svc),
        ):
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
        from common.utils.context_utils import extract_turn_notes_llm

        long_content = "Discuss the deployment of React application " * 50
        mock_response = {
            "keywords": ["React", "deployment", "application"],
            "entities": ["React"],
            "tags": ["deployment"],
            "one_liner": "Discussion about deploying React app",
        }

        mock_openai = MagicMock()
        mock_openai.call_supervisor_llm_json = AsyncMock(return_value=mock_response)

        with patch(
            "common.utils.context_utils.openai_service",
            mock_openai,
            create=True,
        ):
            with patch.dict("sys.modules", {"services.openai_service": MagicMock(openai_service=mock_openai)}):
                result = await extract_turn_notes_llm(long_content)

        assert result is not None
        assert "keywords" in result
        assert len(result["keywords"]) <= 10

    @pytest.mark.asyncio
    async def test_falls_back_to_heuristic_on_llm_failure(self):
        from common.utils.context_utils import extract_turn_notes_llm

        long_content = "Discuss deployment of the application system " * 50

        mock_openai = MagicMock()
        mock_openai.call_supervisor_llm_json = AsyncMock(side_effect=Exception("LLM error"))

        with patch.dict("sys.modules", {"services.openai_service": MagicMock(openai_service=mock_openai)}):
            result = await extract_turn_notes_llm(long_content)

        assert result is not None
        assert "keywords" in result


# =============================================================================
# Memory Search Hydration Tests
# =============================================================================


class TestMemorySearchHydration:
    """Tests for _hydrate_results_from_storage."""

    @pytest.mark.asyncio
    async def test_hydrates_empty_content_from_turn_notes(self, mock_compaction_config):
        from models.search import MemorySearchResult, MemorySourceType
        from services.memory_search_service import MemorySearchService

        service = MemorySearchService()

        results = [
            MemorySearchResult(
                turn_id="turn_1",
                content="",
                content_preview=None,
                role="agent",
                agent_name="TestAgent",
                room_id="room_1",
                source_type=MemorySourceType.TURN,
                vector_score=0.95,
                timestamp=datetime(2026, 2, 20),
            ),
        ]

        async def _mock_cursor():
            yield {
                "turn_id": "turn_1",
                "turn_notes": {
                    "keywords": ["React"],
                    "one_liner": "Discussion about React deployment",
                },
            }

        mock_coll = MagicMock()
        mock_coll.find.return_value = _mock_cursor()

        with patch("database.mongodb.mongodb") as mock_mongodb:
            mock_mongodb.conversation_content_collection = mock_coll
            await service._hydrate_results_from_storage(results, "room_1")

        assert results[0].content == "Discussion about React deployment"
        assert results[0].content_preview == "Discussion about React deployment"

    @pytest.mark.asyncio
    async def test_skips_results_that_already_have_content(self, mock_compaction_config):
        from models.search import MemorySearchResult, MemorySourceType
        from services.memory_search_service import MemorySearchService

        service = MemorySearchService()

        results = [
            MemorySearchResult(
                turn_id="turn_1",
                content="Already has content",
                content_preview="Already has content",
                role="agent",
                room_id="room_1",
                source_type=MemorySourceType.TURN,
                vector_score=0.95,
                timestamp=datetime(2026, 2, 20),
            ),
        ]

        mock_coll = MagicMock()

        with patch("database.mongodb.mongodb") as mock_mongodb:
            mock_mongodb.conversation_content_collection = mock_coll
            await service._hydrate_results_from_storage(results, "room_1")

        mock_coll.find.assert_not_called()


# =============================================================================
# was_successful Propagation Tests
# =============================================================================


class TestWasSuccessfulPropagation:
    """Tests that was_successful is correctly stored on ConversationTurn."""

    def test_add_turn_to_history_preserves_was_successful_true(self):
        mc = MemoryContent()
        result = add_turn_to_history(
            mc,
            role="agent",
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
            role="agent",
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
            role="user",
            content="User message",
        )
        assert len(result.conversation_history) == 1
        assert result.conversation_history[0].was_successful is None


# =============================================================================
# Legacy Turn Token Fallback Tests
# =============================================================================


class TestLegacyTokenFallback:
    """Tests that estimated_tokens_full=0 falls back to estimate_tokens()."""

    def test_select_turns_within_budget_handles_zero_tokens(self, mock_compaction_config):
        from services.context_assembly_service import ContextAssemblyService

        service = ContextAssemblyService()

        legacy_turn = _make_turn(
            content="A " * 500,
            estimated_tokens_full=0,  # Legacy turn with no token estimate
        )
        recent_turn = _make_turn(content="Recent message")

        selected, truncated = service._select_turns_within_budget(
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
        from models.search import MemorySearchResult, MemorySourceType
        from services.context_assembly_service import ContextAssemblyService

        service = ContextAssemblyService()

        search_results = [
            MemorySearchResult(
                turn_id="turn_abc",
                content="User asked about deploying the React frontend",
                content_preview="User asked about deploying the React frontend",
                role="user",
                room_id="room_1",
                source_type=MemorySourceType.TURN,
                vector_score=0.92,
                timestamp=datetime(2026, 2, 20),
            ),
            MemorySearchResult(
                turn_id="turn_def",
                content="",
                content_preview=None,
                role="agent",
                agent_name="DevAgent",
                room_id="room_1",
                source_type=MemorySourceType.TURN,
                vector_score=0.85,
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
