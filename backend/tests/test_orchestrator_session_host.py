"""Focused tests for the process-local orchestrator session host."""

from __future__ import annotations

import asyncio

import pytest

from execution.adapters.session_host import RoomSessionHost
from execution.orchestrator.a2a_runtime.in_memory import InMemoryRoomEpochStore
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
from execution.orchestrator.models import (
    FrozenToolCatalogSnapshot,
    ModelStreamEvent,
    ToolObservation,
    ToolResult,
)
from execution.orchestrator.session import SessionConflict

from ._orchestrator_helpers import (
    NOW,
    ScriptedModelRuntime,
    final_events,
    make_run,
    profile,
    user_message,
)


@pytest.fixture
def catalog() -> FrozenToolCatalogSnapshot:
    return FrozenToolCatalogSnapshot(catalog_id="catalog-1", entries=[], created_at=NOW)


def _host(
    *,
    run_store,
    epoch_store,
    runtime=None,
    listener=None,
):
    def kernel_for_catalog(_snapshot) -> OrchestratorKernel:
        return OrchestratorKernel(
            run_store=run_store,
            model_runtime=runtime,
            tool_runtime=RecordingFakeToolRuntime(),
            tool_catalog=StaticFakeToolCatalog(),
            context_compiler=ContextCompiler(),
            budget_policy=BudgetPolicy(),
            projection_driver=InMemoryProjectionDriver(run_store),
        )

    return RoomSessionHost(
        kernel_factory=kernel_for_catalog,
        run_store=run_store,
        epoch_store=epoch_store,
        listener=listener,
    )


async def test_session_requires_active_epoch_and_rejects_duplicate_rooms(catalog):
    run_store = InMemoryOrchestratorRunStore()
    epoch_store = InMemoryRoomEpochStore()
    host = _host(run_store=run_store, epoch_store=epoch_store)

    with pytest.raises(SessionConflict, match="epoch is not active"):
        await host.create_session(
            room_id="room-1",
            profile=profile(),
            candidate_scope=make_run().candidate_scope,
            requesting_subject_id="user-1",
            frozen_catalog=catalog,
        )

    await epoch_store.activate("room-1", "create-1", activated_at=NOW)
    first = await host.create_session(
        room_id="room-1",
        profile=profile(),
        candidate_scope=make_run().candidate_scope,
        requesting_subject_id="user-1",
        frozen_catalog=catalog,
    )
    assert host.get_session("room-1") is first
    with pytest.raises(SessionConflict, match="already active"):
        await host.create_session(
            room_id="room-1",
            profile=profile(),
            candidate_scope=make_run().candidate_scope,
            requesting_subject_id="user-1",
            frozen_catalog=catalog,
        )
    host.drop_session("room-1")
    assert host.get_session("room-1") is None
    with pytest.raises(SessionConflict, match="no active session"):
        await host.continue_run("room-1")


async def test_prompt_runs_the_kernel_and_forwards_lifecycle_events(catalog):
    run_store = InMemoryOrchestratorRunStore()
    epoch_store = InMemoryRoomEpochStore()
    await epoch_store.activate("room-1", "create-1", activated_at=NOW)
    events = []

    async def listener(event):
        events.append(event.event_type)

    host = _host(
        run_store=run_store,
        epoch_store=epoch_store,
        runtime=ScriptedModelRuntime([final_events("done")]),
        listener=listener,
    )
    await host.create_session(
        room_id="room-1",
        profile=profile(),
        candidate_scope=make_run().candidate_scope,
        requesting_subject_id="user-1",
        frozen_catalog=catalog,
    )

    result = await host.prompt("room-1", user_message(), client_request_id="req-1")

    assert result.outcome == "final_answer"
    assert {"session_started", "run_started", "run_final_answer_ready"} <= set(events)
    assert "session_idle" in events


async def test_observation_sink_reenters_without_a_session_object(catalog):
    run_store = InMemoryOrchestratorRunStore()
    epoch_store = InMemoryRoomEpochStore()
    await epoch_store.activate("room-1", "create-1", activated_at=NOW)
    host = _host(run_store=run_store, epoch_store=epoch_store)

    assert host.observation_sink() is not None
    # Re-entry requires an existing Run; a missing Run is a KeyError.
    with pytest.raises(KeyError):
        await host.observation_sink().deliver(
            "run-missing",
            ToolObservation(
                observation_id="obs-1",
                invocation_id="call-1",
                outcome=ToolResult(
                    call_id="call-1",
                    tool_name="agent",
                    status="completed",
                    content=[],
                    artifact_refs=[],
                ),
                observed_at=NOW,
            ),
        )


class BlockingModelRuntime:
    """Streams an attempt start and then blocks until the task is cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def stream_turn(self, request, *, signal):
        self.started.set()
        yield ModelStreamEvent(kind="attempt_started", attempt=1)
        await asyncio.Event().wait()


async def test_shutdown_cancels_tasks_without_persisting_terminal_state(catalog):
    run_store = InMemoryOrchestratorRunStore()
    epoch_store = InMemoryRoomEpochStore()
    await epoch_store.activate("room-1", "create-1", activated_at=NOW)
    blocking = BlockingModelRuntime()
    host = _host(
        run_store=run_store,
        epoch_store=epoch_store,
        runtime=blocking,
    )
    await host.create_session(
        room_id="room-1",
        profile=profile(),
        candidate_scope=make_run().candidate_scope,
        requesting_subject_id="user-1",
        frozen_catalog=catalog,
    )

    prompt_task = asyncio.create_task(host.prompt("room-1", user_message()))
    await blocking.started.wait()

    await host.shutdown()
    with pytest.raises(asyncio.CancelledError):
        await prompt_task

    # The Run stays non-terminal so recovery workers can re-enter it.
    run_id = run_store.runs
    assert run_id
    run = next(iter(run_id.values()))
    assert run.status == "running"
