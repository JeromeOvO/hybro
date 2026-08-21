"""
Leader-elected orchestrator background workers.

The recovery cycle drains A2A recovery work (cancellation, inbox, call
delivery, artifacts, projection) and the projection worker drains the durable
projection outbox. Both are dark-launch jobs: they are constructed eagerly but
only started when their settings switch is enabled (default OFF in step 6).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from common.config import settings
from common.observability import traced_create_task
from common.utils.logger import get_logger
from jobs.constants import ORCHESTRATOR_PROJECTION, ORCHESTRATOR_RECOVERY

logger = get_logger(__name__)


class LeaderGate(Protocol):
    async def try_acquire(self, name: str, ttl_seconds: int) -> bool: ...

    async def release(self, name: str) -> None: ...


@dataclass(frozen=True)
class OrchestratorRecoveryDeps:
    recover_once: Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class OrchestratorProjectionDeps:
    project_once: Callable[[], Awaitable[int]]


class OrchestratorRecoveryJob:
    def __init__(self, interval_seconds: int = 30) -> None:
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None
        self._leader: LeaderGate | None = None
        self._deps: OrchestratorRecoveryDeps | None = None

    def set_leader_election(self, leader: LeaderGate | None) -> None:
        self._leader = leader

    def set_recovery_deps(self, deps: OrchestratorRecoveryDeps) -> None:
        self._deps = deps

    def _require_deps(self) -> OrchestratorRecoveryDeps:
        if self._deps is None:
            raise RuntimeError("Orchestrator recovery dependencies are not bound")
        return self._deps

    async def start(self) -> None:
        if self._running:
            logger.warning("Orchestrator recovery job already running")
            return
        if not settings.orchestrator_recovery_enabled:
            logger.info("Orchestrator recovery job skipped — flag is disabled")
            return
        self._running = True
        self._task = traced_create_task(self._run_loop(), name="orchestrator-recovery")
        logger.info(
            "Orchestrator recovery job started (interval: %d s)",
            self.interval_seconds,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Orchestrator recovery job stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                await self._run_one_iteration()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Orchestrator recovery job error: %s", exc, exc_info=True)
                await asyncio.sleep(60)

    async def _run_one_iteration(self) -> None:
        deps = self._require_deps()
        if self._leader:
            ttl = int(self.interval_seconds * 2)
            acquired = await self._leader.try_acquire(ORCHESTRATOR_RECOVERY, ttl)
            if not acquired:
                return
            try:
                await deps.recover_once()
            finally:
                await self._leader.release(ORCHESTRATOR_RECOVERY)
        else:
            await deps.recover_once()


class OrchestratorProjectionJob:
    def __init__(self, interval_seconds: int = 30) -> None:
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None
        self._leader: LeaderGate | None = None
        self._deps: OrchestratorProjectionDeps | None = None

    def set_leader_election(self, leader: LeaderGate | None) -> None:
        self._leader = leader

    def set_projection_deps(self, deps: OrchestratorProjectionDeps) -> None:
        self._deps = deps

    def _require_deps(self) -> OrchestratorProjectionDeps:
        if self._deps is None:
            raise RuntimeError("Orchestrator projection dependencies are not bound")
        return self._deps

    async def start(self) -> None:
        if self._running:
            logger.warning("Orchestrator projection job already running")
            return
        if not settings.orchestrator_projection_enabled:
            logger.info("Orchestrator projection job skipped — flag is disabled")
            return
        self._running = True
        self._task = traced_create_task(
            self._run_loop(), name="orchestrator-projection"
        )
        logger.info(
            "Orchestrator projection job started (interval: %d s)",
            self.interval_seconds,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Orchestrator projection job stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                await self._run_one_iteration()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "Orchestrator projection job error: %s", exc, exc_info=True
                )
                await asyncio.sleep(60)

    async def _run_one_iteration(self) -> None:
        deps = self._require_deps()
        if self._leader:
            ttl = int(self.interval_seconds * 2)
            acquired = await self._leader.try_acquire(ORCHESTRATOR_PROJECTION, ttl)
            if not acquired:
                return
            try:
                await deps.project_once()
            finally:
                await self._leader.release(ORCHESTRATOR_PROJECTION)
        else:
            await deps.project_once()


orchestrator_recovery_job = OrchestratorRecoveryJob()
orchestrator_projection_job = OrchestratorProjectionJob()

__all__ = [
    "OrchestratorProjectionDeps",
    "OrchestratorProjectionJob",
    "OrchestratorRecoveryDeps",
    "OrchestratorRecoveryJob",
    "orchestrator_projection_job",
    "orchestrator_recovery_job",
]
