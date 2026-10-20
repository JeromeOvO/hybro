"""C2 step 2: the first-generation orchestration run store is retired."""

from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.run import RunState


def _request(run_id: str | None = "run-legacy") -> SimpleNamespace:
    return SimpleNamespace(
        orchestration_run_id=run_id,
        request_id="request-1",
        room_id="room-1",
        user_message_id="user-1",
        client_request_id="client-1",
    )


@pytest.mark.parametrize(
    "module",
    [
        "execution.orchestration.run_store",
        "models.orchestration",
    ],
)
def test_first_generation_run_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_terminal_lifecycle_adapter_takes_no_orchestration_run_store():
    from execution.hitl.adapters import HITLTerminalLifecycleAdapter

    parameters = inspect.signature(HITLTerminalLifecycleAdapter).parameters

    assert "orchestration_run_store" not in parameters


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_status", "target"),
    [("canceled", RunState.CANCELED), ("expired", RunState.FAILED)],
)
async def test_unhandled_hitl_termination_projects_the_public_run(
    terminal_status, target
):
    from execution.hitl.adapters import HITLTerminalLifecycleAdapter

    lifecycle = MagicMock(project_run_state=AsyncMock())
    adapter = HITLTerminalLifecycleAdapter(
        lifecycle, canonical_terminalizer=AsyncMock(return_value=False)
    )

    await adapter.terminalize_owning_run(
        _request(), terminal_status=terminal_status, reason="why"
    )

    lifecycle.project_run_state.assert_awaited_once_with(
        room_id="room-1",
        run_id="run-legacy",
        trigger_message_id="user-1",
        target_state=target,
        terminal_reason="why",
        causation_id=f"hitl-terminal:request-1:{terminal_status}",
        client_request_id="client-1",
    )


@pytest.mark.asyncio
async def test_hitl_termination_without_a_run_uses_the_user_message_id():
    from execution.hitl.adapters import HITLTerminalLifecycleAdapter

    lifecycle = MagicMock(project_run_state=AsyncMock())
    adapter = HITLTerminalLifecycleAdapter(lifecycle)

    await adapter.terminalize_owning_run(
        _request(run_id=None), terminal_status="canceled", reason="why"
    )

    assert lifecycle.project_run_state.await_args.kwargs["run_id"] == "user-1"


@pytest.mark.asyncio
async def test_canonical_hitl_termination_skips_the_public_projection():
    from execution.hitl.adapters import HITLTerminalLifecycleAdapter

    lifecycle = MagicMock(project_run_state=AsyncMock())
    adapter = HITLTerminalLifecycleAdapter(
        lifecycle, canonical_terminalizer=AsyncMock(return_value=True)
    )

    await adapter.terminalize_owning_run(
        _request(), terminal_status="canceled", reason="why"
    )

    lifecycle.project_run_state.assert_not_awaited()


def test_reconciler_takes_no_orchestration_run_store():
    from execution.hitl.reconciler import HITLLifecycleReconciler

    parameters = inspect.signature(HITLLifecycleReconciler).parameters

    assert "orchestration_run_store" not in parameters


@pytest.mark.asyncio
async def test_reconciler_still_recovers_open_interaction_projections():
    from execution.hitl.reconciler import HITLLifecycleReconciler

    interactions = [
        {"interaction_id": "i-open", "status": "open"},
        {"interaction_id": "i-partial", "status": "partially_answered"},
        {"interaction_id": "i-applying", "status": "applying"},
    ]

    async def iter_active_interactions(*, limit):
        for interaction in interactions:
            yield interaction

    service = MagicMock(recover_open_interaction_projection=AsyncMock(return_value=1))
    reconciler = HITLLifecycleReconciler(
        lifecycle=SimpleNamespace(iter_active_interactions=iter_active_interactions),
        service=service,
        application=MagicMock(),
    )

    assert await reconciler._recover_open_projections() == 2
    recovered = [
        call.args[0]["interaction_id"]
        for call in service.recover_open_interaction_projection.await_args_list
    ]
    assert recovered == ["i-open", "i-partial"]


def test_candidate_scope_snapshot_types_live_in_conversation():
    from conversation.candidate_scope import (
        AuthorizationBasis,
        CandidateAgentSnapshot,
        CandidateScopeSnapshot,
    )

    for model in (AuthorizationBasis, CandidateAgentSnapshot, CandidateScopeSnapshot):
        assert model.__module__ == "conversation.candidate_scope"


def test_container_no_longer_references_the_first_generation_run_store():
    import container

    source = inspect.getsource(container)

    assert "orchestration_runs" not in source
    assert "orchestration_run_events" not in source
    assert "MongoOrchestrationRunStore" not in source
