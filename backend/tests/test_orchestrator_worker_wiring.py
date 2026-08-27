"""Wiring tests for the step-6 orchestrator background jobs."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from common.config.settings import Settings
from jobs.constants import (
    ALL_JOB_NAMES,
    ORCHESTRATOR_PROJECTION,
    ORCHESTRATOR_RECOVERY,
)
from jobs.orchestrator_workers import (
    OrchestratorProjectionDeps,
    OrchestratorProjectionJob,
    OrchestratorRecoveryDeps,
    OrchestratorRecoveryJob,
)


class _FakeLeader:
    def __init__(self) -> None:
        self.acquired: list[tuple[str, int]] = []
        self.released: list[str] = []

    async def try_acquire(self, name: str, ttl_seconds: int) -> bool:
        self.acquired.append((name, ttl_seconds))
        return True

    async def release(self, name: str) -> None:
        self.released.append(name)


def test_job_names_are_registered_for_leader_release_all():
    assert ORCHESTRATOR_RECOVERY in ALL_JOB_NAMES
    assert ORCHESTRATOR_PROJECTION in ALL_JOB_NAMES
    assert ORCHESTRATOR_RECOVERY == "orchestrator_recovery"
    assert ORCHESTRATOR_PROJECTION == "orchestrator_projection"


@pytest.mark.asyncio
async def test_recovery_job_uses_leader_name_and_releases():
    leader = _FakeLeader()
    ran = []

    async def recover_once() -> None:
        ran.append(True)

    job = OrchestratorRecoveryJob(interval_seconds=30)
    job.set_leader_election(leader)
    job.set_recovery_deps(OrchestratorRecoveryDeps(recover_once=recover_once))
    await job._run_one_iteration()

    assert leader.acquired == [(ORCHESTRATOR_RECOVERY, 60)]
    assert leader.released == [ORCHESTRATOR_RECOVERY]
    assert ran == [True]


@pytest.mark.asyncio
async def test_projection_job_uses_leader_name_and_releases():
    leader = _FakeLeader()
    project_once = AsyncMock(return_value=3)
    job = OrchestratorProjectionJob(interval_seconds=15)
    job.set_leader_election(leader)
    job.set_projection_deps(OrchestratorProjectionDeps(project_once=project_once))
    await job._run_one_iteration()

    assert leader.acquired == [(ORCHESTRATOR_PROJECTION, 30)]
    assert leader.released == [ORCHESTRATOR_PROJECTION]
    project_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_jobs_skip_leader_gate_when_no_leader_bound():
    leader = _FakeLeader()
    ran = []

    async def recover_once() -> None:
        ran.append(True)

    job = OrchestratorRecoveryJob(interval_seconds=30)
    job.set_recovery_deps(OrchestratorRecoveryDeps(recover_once=recover_once))
    await job._run_one_iteration()
    assert leader.acquired == []
    assert ran == [True]


def test_mandatory_worker_cadence_defaults_to_thirty_seconds():
    settings = Settings(_env_file=None)
    assert settings.orchestrator_worker_interval_seconds == 30


@pytest.mark.asyncio
async def test_mandatory_workers_start_when_dependencies_are_bound():
    recovery = OrchestratorRecoveryJob(interval_seconds=30)
    recovery.set_recovery_deps(OrchestratorRecoveryDeps(recover_once=AsyncMock()))
    projection = OrchestratorProjectionJob(interval_seconds=30)
    projection.set_projection_deps(
        OrchestratorProjectionDeps(project_once=AsyncMock(return_value=0))
    )

    await recovery.start()
    await projection.start()
    try:
        assert recovery._running is True
        assert projection._running is True
    finally:
        await projection.stop()
        await recovery.stop()


def test_container_wires_orchestrator_jobs_with_leader_gating():
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "container.py").read_text()
    tree = ast.parse(source, filename="container.py")

    required = {
        ("orchestrator_recovery_job", "set_leader_election"),
        ("orchestrator_recovery_job", "start"),
        ("orchestrator_recovery_job", "stop"),
        ("orchestrator_projection_job", "set_leader_election"),
        ("orchestrator_projection_job", "start"),
        ("orchestrator_projection_job", "stop"),
    }
    wired = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id in {
            "orchestrator_recovery_job",
            "orchestrator_projection_job",
        }:
            wired.add((node.value.id, node.attr))

    assert required <= wired
