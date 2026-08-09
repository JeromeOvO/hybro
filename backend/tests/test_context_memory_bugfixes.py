"""
Unit tests for context & memory bug fixes and new functionality.

Covers:
- CompactionSweep: fail-closed safety gate and worker lifecycle
- extract_turn_notes_llm: LLM path and heuristic fallback
- Memory search result hydration: _hydrate_results_from_storage

See docs/System-Architecture.md for the current design.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from common.dto import CompactionResult, MemorySearchResult
from common.types import MessageRole
from common.utils.context_utils import estimate_tokens
from context_memory import search
from context_memory.config import TokenBudgetConfig
from context_memory.models import SearchRankingRecord
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
    with patch("common.config.settings") as mock:
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


def _pending_compaction_workers() -> list[asyncio.Task]:
    return [
        task
        for task in asyncio.all_tasks()
        if not task.done() and task.get_name().startswith("compaction-worker-")
    ]


async def _assert_no_pending_compaction_workers() -> None:
    pending = _pending_compaction_workers()
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    assert not pending, (
        f"Leaked compaction workers: {[task.get_name() for task in pending]}"
    )


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

    @pytest.mark.asyncio
    async def test_sweep_stops_when_active_run_lookup_fails(self):
        """An unavailable run safety gate must abort the entire sweep."""
        from jobs.compaction_sweep import CompactionSweep, CompactionSweepDeps

        sweep = CompactionSweep(interval_minutes=60)
        list_room_ids = AsyncMock(return_value=["room_1"])
        mock_compaction_svc = AsyncMock()
        mock_compaction_svc.compact_if_needed = AsyncMock()
        sweep.set_sweep_deps(
            CompactionSweepDeps(
                list_room_ids_with_memory=list_room_ids,
                get_room_ids_with_non_terminal_runs=AsyncMock(
                    side_effect=RuntimeError("run lookup failed")
                ),
                context_compaction=mock_compaction_svc,
            )
        )

        with pytest.raises(RuntimeError, match="run lookup failed"):
            await sweep.sweep()

        list_room_ids.assert_not_awaited()
        mock_compaction_svc.compact_if_needed.assert_not_awaited()
        await _assert_no_pending_compaction_workers()

    @pytest.mark.asyncio
    async def test_active_run_lookup_failure_releases_leader_lock(self):
        """The iteration wrapper releases leadership when the safety gate fails."""
        from jobs.compaction_sweep import CompactionSweep, CompactionSweepDeps
        from jobs.constants import COMPACTION_SWEEP

        sweep = CompactionSweep(interval_minutes=60)
        leader = MagicMock()
        leader.try_acquire = AsyncMock(return_value=True)
        leader.release = AsyncMock()
        sweep.set_leader_election(leader)

        list_room_ids = AsyncMock(return_value=["room_1"])
        mock_compaction_svc = AsyncMock()
        mock_compaction_svc.compact_if_needed = AsyncMock()
        sweep.set_sweep_deps(
            CompactionSweepDeps(
                list_room_ids_with_memory=list_room_ids,
                get_room_ids_with_non_terminal_runs=AsyncMock(
                    side_effect=RuntimeError("run lookup failed")
                ),
                context_compaction=mock_compaction_svc,
            )
        )

        with pytest.raises(RuntimeError, match="run lookup failed"):
            await sweep._run_one_iteration()

        leader.release.assert_awaited_once_with(COMPACTION_SWEEP)
        list_room_ids.assert_not_awaited()
        mock_compaction_svc.compact_if_needed.assert_not_awaited()
        await _assert_no_pending_compaction_workers()

    @pytest.mark.asyncio
    async def test_sweep_does_not_leak_workers_when_room_enumeration_fails(self):
        """Room enumeration happens before the worker pool is created."""
        from jobs.compaction_sweep import CompactionSweep, CompactionSweepDeps

        sweep = CompactionSweep(interval_minutes=60)
        mock_compaction_svc = AsyncMock()
        mock_compaction_svc.compact_if_needed = AsyncMock()
        sweep.set_sweep_deps(
            CompactionSweepDeps(
                list_room_ids_with_memory=AsyncMock(
                    side_effect=RuntimeError("room enumeration failed")
                ),
                get_room_ids_with_non_terminal_runs=AsyncMock(return_value=[]),
                context_compaction=mock_compaction_svc,
            )
        )

        with pytest.raises(RuntimeError, match="room enumeration failed"):
            await sweep.sweep()

        mock_compaction_svc.compact_if_needed.assert_not_awaited()
        await _assert_no_pending_compaction_workers()

    @pytest.mark.asyncio
    async def test_sweep_cancellation_reaps_working_workers(self):
        """Cancelling the parent sweep cancels and awaits every worker."""
        from jobs.compaction_sweep import (
            MAX_CONCURRENT_COMPACTIONS,
            CompactionSweep,
            CompactionSweepDeps,
        )

        sweep = CompactionSweep(interval_minutes=60)
        all_workers_started = asyncio.Event()
        release_workers = asyncio.Event()
        started_count = 0

        async def blocking_compaction(_room_id: str):
            nonlocal started_count
            started_count += 1
            if started_count == MAX_CONCURRENT_COMPACTIONS:
                all_workers_started.set()
            await release_workers.wait()

        mock_compaction_svc = AsyncMock()
        mock_compaction_svc.compact_if_needed = AsyncMock(
            side_effect=blocking_compaction
        )
        room_ids = [f"room_{index}" for index in range(MAX_CONCURRENT_COMPACTIONS)]
        sweep.set_sweep_deps(
            CompactionSweepDeps(
                list_room_ids_with_memory=AsyncMock(return_value=room_ids),
                get_room_ids_with_non_terminal_runs=AsyncMock(return_value=[]),
                context_compaction=mock_compaction_svc,
            )
        )

        sweep_task = asyncio.create_task(sweep.sweep())
        worker_tasks: list[asyncio.Task] = []
        try:
            await asyncio.wait_for(all_workers_started.wait(), timeout=5)
            worker_tasks = _pending_compaction_workers()
            assert len(worker_tasks) == MAX_CONCURRENT_COMPACTIONS

            sweep_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await sweep_task

            assert all(task.cancelled() for task in worker_tasks)
            await _assert_no_pending_compaction_workers()
        finally:
            release_workers.set()
            if not sweep_task.done():
                sweep_task.cancel()
            await asyncio.gather(sweep_task, return_exceptions=True)
            await _assert_no_pending_compaction_workers()

    @pytest.mark.asyncio
    async def test_sweep_continues_after_single_room_failure(self):
        """A room-local compaction error is counted without aborting the sweep."""
        from jobs.compaction_sweep import CompactionSweep, CompactionSweepDeps

        sweep = CompactionSweep(interval_minutes=60)

        async def compact_room(room_id: str):
            if room_id == "bad_room":
                raise RuntimeError("compaction failed")
            return CompactionResult(
                room_id=room_id,
                compacted_count=1,
                tokens_saved=100,
            )

        mock_compaction_svc = AsyncMock()
        mock_compaction_svc.compact_if_needed = AsyncMock(side_effect=compact_room)
        sweep.set_sweep_deps(
            CompactionSweepDeps(
                list_room_ids_with_memory=AsyncMock(
                    return_value=["bad_room", "good_room_1", "good_room_2"]
                ),
                get_room_ids_with_non_terminal_runs=AsyncMock(return_value=[]),
                context_compaction=mock_compaction_svc,
            )
        )

        stats = await sweep.sweep()

        assert stats == {"scanned": 3, "compacted": 2, "skipped": 0, "errors": 1}
        assert mock_compaction_svc.compact_if_needed.await_count == 3
        mock_compaction_svc.compact_if_needed.assert_has_awaits(
            [call("bad_room"), call("good_room_1"), call("good_room_2")],
            any_order=True,
        )

    @pytest.mark.asyncio
    async def test_sweep_limits_compaction_concurrency(self):
        """The fixed worker pool never exceeds its configured concurrency."""
        from jobs.compaction_sweep import (
            MAX_CONCURRENT_COMPACTIONS,
            CompactionSweep,
            CompactionSweepDeps,
        )

        sweep = CompactionSweep(interval_minutes=60)
        in_flight = 0
        max_in_flight = 0

        async def track_concurrency(_room_id: str):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            try:
                await asyncio.sleep(0)
            finally:
                in_flight -= 1

        mock_compaction_svc = AsyncMock()
        mock_compaction_svc.compact_if_needed = AsyncMock(side_effect=track_concurrency)
        room_ids = [f"room_{index}" for index in range(MAX_CONCURRENT_COMPACTIONS * 2)]
        sweep.set_sweep_deps(
            CompactionSweepDeps(
                list_room_ids_with_memory=AsyncMock(return_value=room_ids),
                get_room_ids_with_non_terminal_runs=AsyncMock(return_value=[]),
                context_compaction=mock_compaction_svc,
            )
        )

        stats = await sweep.sweep()

        assert stats["scanned"] == len(room_ids)
        assert max_in_flight == MAX_CONCURRENT_COMPACTIONS


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
            == "context_memory_json_model"
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
# Legacy Turn Token Fallback Tests
# =============================================================================


class TestLegacyTokenFallback:
    """Tests that estimated_tokens_full=0 falls back to estimate_tokens()."""

    def test_select_turns_within_budget_handles_zero_tokens(
        self, mock_compaction_config
    ):
        from context_memory.assembly import select_turns_within_budget
        from context_memory.translators import turn_from_dict

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

        selected, truncated = select_turns_within_budget(
            [
                turn_from_dict(legacy_turn.model_dump(mode="json")),
                turn_from_dict(recent_turn.model_dump(mode="json")),
            ],
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

    def test_search_results_appear_in_supervisor_context(self):
        from context_memory.assembly import assemble_supervisor_context_from_memory

        search_results = [
            MemorySearchResult(
                content="User asked about deploying the React frontend",
                room_id="room_1",
                keyword_score=0.92,
                relevance_score=0.92,
                temporal_decay_factor=1.0,
                source_message_id="turn_abc",
                metadata={
                    "content_preview": "User asked about deploying the React frontend",
                    "role": MessageRole.USER,
                },
            ),
            MemorySearchResult(
                content="",
                room_id="room_1",
                keyword_score=0.85,
                relevance_score=0.85,
                temporal_decay_factor=1.0,
                source_message_id="turn_def",
                metadata={"role": MessageRole.AGENT, "agent_name": "DevAgent"},
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

        result = assemble_supervisor_context_from_memory(
            room_memory.model_dump(mode="json"),
            "Deploy the app",
            token_budget=TokenBudgetConfig(
                model_context_window=32000,
                system_prompt=2000,
                tool_schemas=3000,
                response_reserve=4000,
                room_context_pct=0.15,
                conversation_history_pct=0.60,
                current_task_pct=0.25,
            ),
            memory_search_results=search_results,
        )

        context = result.metadata["context"]
        assert "deploying the React frontend" in context
        assert "[Relevant Memory]" in context
        # Empty-content result should NOT appear (no preview)
        assert "DevAgent" not in context
