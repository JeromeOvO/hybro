"""
Stale Task Checker Background Job

This module provides a leader-gated background job that:
1. Runs the durable HITL lifecycle reconciliation passes
2. Cleans up stuck legacy room processing status
3. Recovers HITL requests stuck in "processing"
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from common.observability import traced_create_task
from common.utils.logger import get_logger
from jobs.constants import STALE_TASK_CHECKER

logger = get_logger(__name__)


class LeaderGate(Protocol):
    async def try_acquire(self, name: str, ttl_seconds: int) -> bool: ...

    async def release(self, name: str) -> None: ...


@dataclass(frozen=True)
class StaleHITLDeps:
    recover_stale_processing: Callable[[], Awaitable[Any]]
    reconcile_lifecycle: Callable[[], Awaitable[Any]] | None = None


@dataclass(frozen=True)
class StaleTaskCheckerDeps:
    store: Any
    rooms_collection: Any


class StaleTaskChecker:
    """Background job for HITL reconciliation and legacy room-status cleanup.

    Canonical Run recovery and projection are owned exclusively by the
    mandatory orchestrator workers; this checker never creates a second
    Run/Task lifecycle.
    """

    def __init__(self, check_interval_minutes: int = 5):
        self.check_interval_minutes = check_interval_minutes
        self._running = False
        self._task: asyncio.Task | None = None
        self._leader: LeaderGate | None = None
        self._hitl_deps: StaleHITLDeps | None = None
        self._runtime_deps: StaleTaskCheckerDeps | None = None

    def set_leader_election(self, leader: LeaderGate | None) -> None:
        """Attach a leader gate instance for distributed leader gating."""
        self._leader = leader

    def set_runtime_deps(self, deps: StaleTaskCheckerDeps) -> None:
        self._runtime_deps = deps

    def _deps(self) -> StaleTaskCheckerDeps:
        if self._runtime_deps is None:
            raise RuntimeError("Stale task checker runtime deps are not bound")
        return self._runtime_deps

    @property
    def _store(self) -> Any:
        return self._deps().store

    @property
    def _rooms_collection(self) -> Any:
        return self._deps().rooms_collection

    def set_hitl_deps(self, deps: StaleHITLDeps) -> None:
        self._hitl_deps = deps

    async def start(self) -> None:
        """Start the background checker."""
        if self._running:
            logger.warning("Stale task checker already running")
            return

        self._running = True
        self._task = traced_create_task(self._run_loop(), name="stale-task-checker")
        logger.info(
            f"Stale task checker started (interval: {self.check_interval_minutes}min)"
        )

    async def stop(self) -> None:
        """Stop the background checker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Stale task checker stopped")

    async def _run_loop(self) -> None:
        """Main loop that runs the check periodically."""
        while self._running:
            try:
                await self._run_one_iteration()
            except Exception as exc:
                logger.error(
                    "stale_task_checker_failed",
                    extra={"error_type": type(exc).__name__},
                )

            # Wait for next check
            await asyncio.sleep(self.check_interval_minutes * 60)

    async def _run_one_iteration(self) -> None:
        """Run a single iteration, gated by leader election if available."""
        if self._leader:
            ttl = int(self.check_interval_minutes * 60 * 2)
            acquired = await self._leader.try_acquire(STALE_TASK_CHECKER, ttl)
            if not acquired:
                return  # another instance is the leader
            try:
                await self.check_stale_tasks()
            finally:
                await self._leader.release(STALE_TASK_CHECKER)
        else:
            await self.check_stale_tasks()

    async def check_stale_tasks(self) -> None:
        """Run one reconciliation pass; called periodically by the loop."""
        # Aggregate deadlines, durable applications, and command uncertainty are
        # authoritative and converge first.
        if self._hitl_deps and self._hitl_deps.reconcile_lifecycle is not None:
            try:
                await self._hitl_deps.reconcile_lifecycle()
            except Exception:
                logger.exception("HITL lifecycle reconciliation failed")

        await self._cleanup_stuck_processing_status()

        # Recover HITL requests stuck in "processing" (worker crashed between
        # CAS claim and finalization).
        try:
            if self._hitl_deps is None:
                logger.warning(
                    "Skipped stale HITL processing recovery: HITL deps are not bound"
                )
            else:
                await self._hitl_deps.recover_stale_processing()
        except Exception as e:
            logger.error("Failed to recover stale HITL processing requests: %s", e)

    async def _cleanup_stuck_processing_status(self) -> None:
        """
        Null legacy rooms.processing_message_id when there are no non-terminal runs.

        Mirrors are no longer written on the lifecycle path; this job clears stale
        Mongo values when the legacy ``runs`` mirror has no non-terminal run for
        the room.
        """
        try:
            busy_ids = await self._store.get_room_ids_with_non_terminal_runs()
        except Exception as e:
            logger.warning(
                "legacy processing_message_id cleanup: could not list active rooms: %s",
                e,
            )
            return

        busy = list({rid for rid in busy_ids if rid})
        flt: dict = {"processing_message_id": {"$ne": None}}
        if busy:
            flt["room_id"] = {"$nin": busy}
        try:
            coll = self._rooms_collection
            if coll is None:
                raise RuntimeError("rooms_collection is not bound")
            res = await coll.update_many(flt, {"$set": {"processing_message_id": None}})
            modified_count = getattr(res, "modified_count", res)
            if modified_count:
                logger.info(
                    "legacy processing_message_id cleanup: nulled field on %d rooms "
                    "(no non-terminal runs)",
                    modified_count,
                )
        except Exception as e:
            logger.warning("legacy processing_message_id cleanup failed: %s", e)


# Singleton instance. Application startup binds runtime dependencies.
stale_task_checker = StaleTaskChecker()
