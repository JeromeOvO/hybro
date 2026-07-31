"""
Background Compaction Sweep Job

Periodically scans all active rooms and runs lossless compaction on any
room whose conversation history exceeds the configured thresholds.

This catches rooms that grow without triggering inline compaction — e.g.
supervisor orchestration rooms, rooms with high-frequency direct chat, or rooms
where the inline trigger was skipped due to an error.

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6 for compaction design.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from common.observability import traced_create_task
from common.utils.logger import get_logger
from context_memory.protocols import ContextMemoryCompactionPort
from jobs.constants import COMPACTION_SWEEP
from models.context_config import compaction_config

logger = get_logger(__name__)

DEFAULT_INTERVAL_MINUTES = 30
MAX_CONCURRENT_COMPACTIONS = 5


class LeaderGate(Protocol):
    async def try_acquire(self, name: str, ttl_seconds: int) -> bool: ...

    async def release(self, name: str) -> None: ...


@dataclass(frozen=True)
class CompactionSweepDeps:
    list_room_ids_with_memory: Callable[[], Awaitable[list[str]]]
    get_room_ids_with_non_terminal_runs: Callable[[], Awaitable[list[str]]]
    context_compaction: ContextMemoryCompactionPort


class CompactionSweep:
    """Background job that sweeps rooms for compaction eligibility."""

    def __init__(self, interval_minutes: int = DEFAULT_INTERVAL_MINUTES):
        self.interval_minutes = interval_minutes
        self._running = False
        self._task: asyncio.Task | None = None
        self._leader: LeaderGate | None = None
        self._deps: CompactionSweepDeps | None = None

    def set_leader_election(self, leader: LeaderGate | None) -> None:
        """Attach a LeaderElection instance for distributed leader gating."""
        self._leader = leader

    def set_sweep_deps(self, deps: CompactionSweepDeps) -> None:
        self._deps = deps

    def _require_sweep_deps(self) -> CompactionSweepDeps:
        if self._deps is None:
            raise RuntimeError("Compaction sweep dependencies are not bound")
        return self._deps

    async def start(self) -> None:
        if self._running:
            logger.warning("Compaction sweep already running")
            return
        if not compaction_config.enabled:
            logger.info("Compaction sweep skipped — compaction is disabled")
            return
        self._running = True
        self._task = traced_create_task(self._run_loop(), name="compaction-sweep")
        logger.info(
            "Compaction sweep started (interval: %d min)", self.interval_minutes
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Compaction sweep stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_minutes * 60)
                await self._run_one_iteration()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Compaction sweep error: %s", e, exc_info=True)
                await asyncio.sleep(60)

    async def _run_one_iteration(self) -> None:
        """Run a single iteration, gated by leader election if available."""
        if self._leader:
            ttl = int(self.interval_minutes * 60 * 2)
            acquired = await self._leader.try_acquire(COMPACTION_SWEEP, ttl)
            if not acquired:
                return  # another instance is the leader
            try:
                await self.sweep()
            finally:
                await self._leader.release(COMPACTION_SWEEP)
        else:
            await self.sweep()

    async def sweep(self) -> dict:
        """Scan all rooms with memory and compact where needed.

        Skips rooms with any non-terminal run (queued / processing / awaiting_input)
        to avoid read-modify-write races with the supervisor loop (§6.9).

        Uses a fixed-size worker pool to bound memory usage instead of
        creating one task per room.

        Returns a summary dict: {scanned, compacted, skipped, errors}.
        """
        deps = self._require_sweep_deps()
        stats = {"scanned": 0, "compacted": 0, "skipped": 0, "errors": 0}

        # Pre-fetch room_ids with non-terminal runs (runs are source of truth)
        active_room_ids: set[str] = set()
        try:
            ids = await deps.get_room_ids_with_non_terminal_runs()
            active_room_ids = {rid for rid in ids if rid}
        except Exception as e:
            logger.warning("Compaction sweep: could not check active rooms: %s", e)

        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def _worker() -> None:
            while True:
                rid = await queue.get()
                if rid is None:
                    queue.task_done()
                    return
                try:
                    result = await deps.context_compaction.compact_if_needed(rid)
                    if result and result.compacted_count > 0:
                        stats["compacted"] += 1
                        logger.info(
                            "compaction_sweep: compacted room %s "
                            "(%d turns, saved ~%d tokens)",
                            rid,
                            result.compacted_count,
                            result.tokens_saved,
                        )
                except Exception as exc:
                    stats["errors"] += 1
                    logger.warning(
                        "compaction_sweep: error compacting room %s: %s",
                        rid,
                        exc,
                    )
                finally:
                    queue.task_done()

        workers = [
            traced_create_task(_worker(), name=f"compaction-worker-{index}")
            for index in range(MAX_CONCURRENT_COMPACTIONS)
        ]

        for room_id in await deps.list_room_ids_with_memory():
            if not room_id:
                continue
            stats["scanned"] += 1

            if room_id in active_room_ids:
                stats["skipped"] += 1
                continue

            await queue.put(room_id)

        # Signal workers to stop
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers)

        logger.info(
            "compaction_sweep: done — scanned=%d compacted=%d skipped=%d errors=%d",
            stats["scanned"],
            stats["compacted"],
            stats["skipped"],
            stats["errors"],
        )
        return stats


compaction_sweep = CompactionSweep()
