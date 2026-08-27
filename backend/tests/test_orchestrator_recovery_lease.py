import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from execution.orchestrator.budget import BudgetPolicy
from execution.orchestrator.context import ContextCompiler
from execution.orchestrator.fake_tools import (
    RecordingFakeToolRuntime,
    StaticFakeToolCatalog,
)
from execution.orchestrator.in_memory import (
    InMemoryOrchestratorRunStore,
    InMemoryProjectionDriver,
)
from execution.orchestrator.kernel import OrchestratorKernel
from execution.orchestrator.session import EventCancellationSignal
from orchestrator_composition import _run_with_recovery_lease
from tests._orchestrator_helpers import (
    FixedClock,
    FixedIDs,
    ScriptedModelRuntime,
    final_events,
    make_run,
)


@pytest.mark.asyncio
async def test_long_healthy_provider_window_is_not_due_on_recovery_tick():
    store = InMemoryOrchestratorRunStore()
    run = make_run()
    assert run.recovery_claim.next_attempt_at == run.budget.deadline_at
    assert (await store.create(run, command_id="create")).outcome == "accepted"

    provider_finished = asyncio.Event()

    async def healthy_provider_stream():
        await asyncio.sleep(0.05)
        provider_finished.set()

    provider = asyncio.create_task(healthy_provider_stream())
    for offset in (0, 1, 30):
        due = await store.list_due_runs(
            due_at=run.created_at + timedelta(seconds=offset), limit=10
        )
        assert due == []
        await asyncio.sleep(0.01)
    await provider
    assert provider_finished.is_set()


@pytest.mark.asyncio
async def test_two_workers_slow_runtime_keeps_single_recovery_owner_past_lease():
    store = InMemoryOrchestratorRunStore()
    run = make_run()
    assert (await store.create(run, command_id="create")).outcome == "accepted"
    owner_one = "instance-one:token-one"
    owner_two = "instance-two:token-two"
    claimed = await store.claim_recovery(
        run.run_id,
        expected_state_version=run.state_version,
        owner_id=owner_one,
        lease_expires_at=datetime.now(UTC) + timedelta(milliseconds=40),
    )
    assert claimed.outcome == "accepted"
    side_effects: list[str] = []

    async def slow_runtime():
        side_effects.append("provider-called")
        await asyncio.sleep(0.14)
        return "done"

    worker_one = asyncio.create_task(
        _run_with_recovery_lease(
            run_store=store,
            run_id=run.run_id,
            owner_id=owner_one,
            work=slow_runtime(),
            lease_duration=timedelta(milliseconds=40),
            renew_interval_seconds=0.01,
        )
    )
    await asyncio.sleep(0.08)
    due = await store.list_due_runs(due_at=datetime.now(UTC), limit=10)
    assert due == []
    latest = await store.load(run.run_id)
    assert latest is not None
    stolen = (
        await store.claim_recovery(
            run.run_id,
            expected_state_version=latest.state_version,
            owner_id=owner_two,
            lease_expires_at=datetime.now(UTC) + timedelta(milliseconds=40),
        )
        if due
        else None
    )
    assert stolen is None
    assert await worker_one == "done"
    assert side_effects == ["provider-called"]
    latest = await store.load(run.run_id)
    assert latest is not None and latest.recovery_claim.owner_id == owner_one


@pytest.mark.asyncio
async def test_slow_kernel_checkpoint_commits_while_recovery_lease_renews():
    store = InMemoryOrchestratorRunStore()
    run = make_run()
    assert (await store.create(run, command_id="create")).outcome == "accepted"
    owner = "instance:slow-kernel"
    claimed = await store.claim_recovery(
        run.run_id,
        expected_state_version=run.state_version,
        owner_id=owner,
        lease_expires_at=datetime.now(UTC) + timedelta(milliseconds=30),
    )
    assert claimed.run is not None
    claimed_version = claimed.run.state_version

    class SlowRuntime(ScriptedModelRuntime):
        async def stream_turn(self, request, *, signal):
            async for event in super().stream_turn(request, signal=signal):
                await asyncio.sleep(0.035)
                yield event

    kernel = OrchestratorKernel(
        run_store=store,
        model_runtime=SlowRuntime([final_events("lease-safe")]),
        tool_runtime=RecordingFakeToolRuntime(),
        tool_catalog=StaticFakeToolCatalog(),
        context_compiler=ContextCompiler(),
        budget_policy=BudgetPolicy(),
        projection_driver=InMemoryProjectionDriver(store),
        clock=FixedClock(),
        id_factory=FixedIDs(),
    )
    result = await _run_with_recovery_lease(
        run_store=store,
        run_id=run.run_id,
        owner_id=owner,
        work=kernel.run(run.run_id, signal=EventCancellationSignal()),
        lease_duration=timedelta(milliseconds=30),
        renew_interval_seconds=0.01,
    )

    assert result.outcome == "final_answer"
    latest = await store.load(run.run_id)
    assert latest is not None
    assert latest.status == "completed"
    assert latest.state_version > claimed_version
