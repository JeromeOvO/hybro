"""
Leader-elected orchestrator background workers.

The recovery cycle drains A2A recovery work (cancellation, inbox, call
delivery, artifacts, projection), the projection worker drains the durable
projection outbox, and the canary job reads the durable stores and logs a
warning when a §8.2 threshold is exceeded. All three are dark-launch jobs:
they are constructed eagerly but only started when their settings switch is
enabled (default OFF until steps 6/8).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from common.config import settings
from common.observability import traced_create_task
from common.utils.logger import get_logger
from execution.adapters.canary_metrics import evaluate_canary_thresholds
from jobs.constants import (
    ORCHESTRATOR_CANARY,
    ORCHESTRATOR_PROJECTION,
    ORCHESTRATOR_RECOVERY,
)

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


@dataclass(frozen=True)
class OrchestratorCanaryDeps:
    collect: Callable[[], Awaitable[dict[str, Any]]]


class OrchestratorRecoveryJob:
    def __init__(self, interval_seconds: int = 30) -> None:
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None
        self._leader: LeaderGate | None = None
        self._deps: OrchestratorRecoveryDeps | None = None
        self.last_completed_at: datetime | None = None

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
        self.last_completed_at = datetime.now(UTC)


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


class OrchestratorCanaryJob:
    """Leader-elected canary threshold watcher (flag-gated, default OFF)."""

    def __init__(self, interval_seconds: int = 30) -> None:
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None
        self._leader: LeaderGate | None = None
        self._deps: OrchestratorCanaryDeps | None = None

    def set_leader_election(self, leader: LeaderGate | None) -> None:
        self._leader = leader

    def set_canary_deps(self, deps: OrchestratorCanaryDeps) -> None:
        self._deps = deps

    def _require_deps(self) -> OrchestratorCanaryDeps:
        if self._deps is None:
            raise RuntimeError("Orchestrator canary dependencies are not bound")
        return self._deps

    async def start(self) -> None:
        if self._running:
            logger.warning("Orchestrator canary job already running")
            return
        if not settings.orchestrator_canary_enabled:
            logger.info("Orchestrator canary job skipped — flag is disabled")
            return
        self._running = True
        self._task = traced_create_task(self._run_loop(), name="orchestrator-canary")
        logger.info(
            "Orchestrator canary job started (interval: %d s)",
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
        logger.info("Orchestrator canary job stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                await self._run_one_iteration()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Orchestrator canary job error: %s", exc, exc_info=True)
                await asyncio.sleep(60)

    async def _run_one_iteration(self) -> None:
        deps = self._require_deps()
        if self._leader:
            ttl = int(self.interval_seconds * 2)
            acquired = await self._leader.try_acquire(ORCHESTRATOR_CANARY, ttl)
            if not acquired:
                return
            try:
                await self._evaluate(deps)
            finally:
                await self._leader.release(ORCHESTRATOR_CANARY)
        else:
            await self._evaluate(deps)

    async def _evaluate(self, deps: OrchestratorCanaryDeps) -> None:
        metrics = await deps.collect()
        for message in evaluate_canary_thresholds(metrics, settings):
            logger.warning(message)


orchestrator_recovery_job = OrchestratorRecoveryJob()
orchestrator_projection_job = OrchestratorProjectionJob()
orchestrator_canary_job = OrchestratorCanaryJob()

__all__ = [
    "OrchestratorCanaryDeps",
    "OrchestratorCanaryJob",
    "OrchestratorProjectionDeps",
    "OrchestratorProjectionJob",
    "OrchestratorRecoveryDeps",
    "OrchestratorRecoveryJob",
    "orchestrator_canary_job",
    "orchestrator_projection_job",
    "orchestrator_recovery_job",
]
