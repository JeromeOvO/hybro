"""
Unit tests for context & memory bug fixes and new functionality.

Covers:
- CompactionSweep: fail-closed safety gate and worker lifecycle

See docs/System-Architecture.md for the current design.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from common.dto import CompactionResult
from common.utils.context_utils import estimate_tokens, get_context_stats
from context_memory.models import ConversationTurnData

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_compaction_config():
    with patch("common.config.loader") as mock:
        mock.compaction_enabled = True
        mock.compaction_max_full_turns = 20
        mock.compaction_max_total_tokens = 80000
        mock.compaction_preserve_recent = 10
        mock.compaction_content_ttl_days = 0
        yield mock


def _make_turn(content="Test", role="user", **kwargs) -> ConversationTurnData:
    defaults = dict(
        turn_id=str(uuid4()),
        role=role,
        content=content,
        content_type="text",
        representation="full",
        estimated_tokens_full=estimate_tokens(content),
        estimated_tokens_compact=20,
        timestamp=datetime(2026, 2, 20),
    )
    defaults.update(kwargs)
    return ConversationTurnData(**defaults)


def test_context_stats_use_canonical_history_and_nested_summary_only():
    summary = "Canonical room summary"
    canonical_full = _make_turn(content="canonical full", estimated_tokens_full=3)
    canonical_compact = _make_turn(
        content=None,
        representation="compact",
        brief_summary="canonical compact",
        estimated_tokens_full=50,
        estimated_tokens_compact=12,
    )
    room_memory = SimpleNamespace(
        memory_content=SimpleNamespace(
            summary=summary,
            conversation_history=[_make_turn(content="nested history is ignored")],
        ),
        conversation_history=[canonical_full, canonical_compact],
    )

    stats = get_context_stats(room_memory)

    assert stats == {
        "history_turns": 2,
        "full_turns": 1,
        "compact_turns": 1,
        "has_summary": True,
        "summary_length": len(summary),
        "total_chars": len(summary) + len("canonical full"),
        "total_tokens": 53,
    }


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
            conversation_id="room_1",
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
            conversation_id="idle_room",
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

        async def blocking_compaction(_conversation_id: str):
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

        async def compact_room(conversation_id: str):
            if conversation_id == "bad_room":
                raise RuntimeError("compaction failed")
            return CompactionResult(
                conversation_id=conversation_id,
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

        async def track_concurrency(_conversation_id: str):
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
