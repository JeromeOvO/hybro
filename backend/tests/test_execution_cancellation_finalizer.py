import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.utils.cancellation import CancellationToken
from execution.cancellation.finalizer import CancellationFinalizer
from execution.cancellation.runtime import CancellationPropagationResult
from execution.orchestration.run_store import InMemoryOrchestrationRunStore
from models.orchestration import (
    OrchestrationEventType,
    OrchestrationRunState,
    OrchestrationStatus,
)


def _state(status: OrchestrationStatus) -> OrchestrationRunState:
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="message-1",
        goal="Cancel safely",
        candidate_agent_ids=["agent-1"],
        status=status,
        pending_hitl_request_ids=["hitl-1"],
        open_questions=[{"request_id": "hitl-1", "status": "open"}],
    )


def _finalizer(store=None, **overrides):
    deps = {
        "project_status": AsyncMock(return_value=True),
        "broadcast_cancellation": AsyncMock(),
        "get_active_token": MagicMock(return_value=None),
        "release_active_token": MagicMock(return_value=True),
        "clear_cancellation": MagicMock(),
        "cancel_hitl": AsyncMock(),
        "project_public_terminal": AsyncMock(),
        "cleanup_agent_tasks": AsyncMock(),
        "mark_reconciled": AsyncMock(return_value=True),
        "get_public_run": AsyncMock(return_value=None),
    }
    deps.update(overrides)
    return (
        CancellationFinalizer(run_store=store, **deps),
        deps,
    )


@pytest.mark.asyncio
async def test_finalizer_cancels_awaiting_run_and_all_surfaces():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(_state(OrchestrationStatus.AWAITING_USER))
    finalizer, deps = _finalizer(store)

    result = await finalizer.finalize(room_id="room-1", message_id="message-1")

    saved = await store.get_run("run-1")
    assert saved is not None
    assert saved.status == OrchestrationStatus.CANCELED
    assert saved.pending_hitl_request_ids == []
    assert saved.open_questions[0]["status"] == "canceled"
    assert result.cancellation_applied is True
    assert result.reconciled is True
    events = store._events_by_run["run-1"]
    assert [event.type for event in events] == [OrchestrationEventType.RUN_TERMINAL]
    deps["broadcast_cancellation"].assert_awaited_once_with("message-1")
    deps["cancel_hitl"].assert_awaited_once_with("message-1")
    deps["project_public_terminal"].assert_awaited_once_with(
        room_id="room-1",
        message_id="message-1",
        status=OrchestrationStatus.CANCELED,
    )
    assert deps["cleanup_agent_tasks"].await_count == 2
    deps["cleanup_agent_tasks"].assert_awaited_with(
        room_id="room-1", message_id="message-1"
    )
    deps["mark_reconciled"].assert_awaited_once_with("message-1")
    deps["clear_cancellation"].assert_called_once_with("message-1")
    deps["release_active_token"].assert_called_once_with("message-1", None)


@pytest.mark.asyncio
async def test_finalizer_preserves_completion_winner_without_destructive_effects():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(_state(OrchestrationStatus.COMPLETED))
    finalizer, deps = _finalizer(store)

    result = await finalizer.finalize(room_id="room-1", message_id="message-1")

    assert result.status == OrchestrationStatus.COMPLETED
    assert result.cancellation_applied is False
    deps["project_status"].assert_awaited_once_with(
        room_id="room-1",
        message_id="message-1",
        status=OrchestrationStatus.COMPLETED,
    )
    deps["broadcast_cancellation"].assert_not_awaited()
    deps["cancel_hitl"].assert_not_awaited()
    deps["project_public_terminal"].assert_awaited_once_with(
        room_id="room-1",
        message_id="message-1",
        status=OrchestrationStatus.COMPLETED,
    )
    deps["cleanup_agent_tasks"].assert_not_awaited()
    deps["mark_reconciled"].assert_awaited_once_with("message-1")
    deps["clear_cancellation"].assert_called_once_with("message-1")
    deps["release_active_token"].assert_not_called()


@pytest.mark.asyncio
async def test_failed_effect_leaves_marker_pending_and_retry_finishes():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(_state(OrchestrationStatus.RUNNING))
    cleanup = AsyncMock(side_effect=[RuntimeError("temporary"), None, None])
    finalizer, deps = _finalizer(store, cleanup_agent_tasks=cleanup)

    with pytest.raises(RuntimeError, match="temporary"):
        await finalizer.finalize(room_id="room-1", message_id="message-1")
    deps["mark_reconciled"].assert_not_awaited()

    result = await finalizer.finalize(room_id="room-1", message_id="message-1")

    assert result.reconciled is True
    assert len(store._events_by_run["run-1"]) == 1
    deps["mark_reconciled"].assert_awaited_once_with("message-1")


@pytest.mark.asyncio
async def test_failed_propagation_preserves_tombstone_and_concurrent_resume_token():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(_state(OrchestrationStatus.RUNNING))
    old_token = CancellationToken(message_id="message-1")
    tokens = {"message-1": old_token}
    tombstones: set[str] = set()
    broadcast_started = asyncio.Event()
    allow_broadcast_return = asyncio.Event()

    def get_active_token(message_id):
        return tokens.get(message_id)

    def release_active_token(message_id, expected):
        if tokens.get(message_id) is not expected:
            return False
        tokens.pop(message_id)
        return True

    def clear_cancellation(message_id):
        tombstones.discard(message_id)

    async def failed_broadcast(message_id):
        tombstones.add(message_id)
        old_token.cancel()
        broadcast_started.set()
        await allow_broadcast_return.wait()
        return CancellationPropagationResult(
            kv_configured=True,
            kv_succeeded=False,
            pubsub_configured=True,
            pubsub_succeeded=False,
        )

    finalizer, deps = _finalizer(
        store,
        get_active_token=MagicMock(side_effect=get_active_token),
        release_active_token=MagicMock(side_effect=release_active_token),
        clear_cancellation=MagicMock(side_effect=clear_cancellation),
        broadcast_cancellation=AsyncMock(side_effect=failed_broadcast),
    )
    finalize_task = asyncio.create_task(
        finalizer.finalize(room_id="room-1", message_id="message-1")
    )
    await broadcast_started.wait()

    # The canceled owner exits while a resume concurrently installs a new token.
    assert release_active_token("message-1", old_token) is True
    resumed_token = CancellationToken(message_id="message-1")
    if "message-1" in tombstones:
        resumed_token.cancel()
    tokens["message-1"] = resumed_token
    allow_broadcast_return.set()

    result = await finalize_task

    assert result.reconciled is False
    assert tokens["message-1"] is resumed_token
    assert resumed_token.is_cancelled is True
    assert "message-1" in tombstones
    deps["mark_reconciled"].assert_not_awaited()
    deps["clear_cancellation"].assert_not_called()
    deps["release_active_token"].assert_called_once_with("message-1", old_token)


@pytest.mark.asyncio
async def test_no_run_preserves_completed_public_lifecycle():
    finalizer, deps = _finalizer(
        None,
        get_public_run=AsyncMock(
            return_value=type("PublicRun", (), {"state": "completed"})()
        ),
    )

    result = await finalizer.finalize(
        room_id="room-1",
        message_id="message-1",
    )

    assert result.status == OrchestrationStatus.COMPLETED
    deps["broadcast_cancellation"].assert_not_awaited()
    deps["project_public_terminal"].assert_not_awaited()
    deps["cleanup_agent_tasks"].assert_not_awaited()
    deps["mark_reconciled"].assert_awaited_once_with("message-1")


@pytest.mark.asyncio
async def test_settlement_rechecks_and_cancels_late_created_run():
    store = InMemoryOrchestrationRunStore()

    async def create_late_run(_message_id):
        await store.create_run(_state(OrchestrationStatus.CREATED))

    finalizer, deps = _finalizer(
        store,
        broadcast_cancellation=AsyncMock(side_effect=create_late_run),
    )

    result = await finalizer.finalize(
        room_id="room-1",
        message_id="message-1",
        settle_no_run=True,
    )

    saved = await store.get_run("run-1")
    assert saved is not None
    assert saved.status == OrchestrationStatus.CANCELED
    assert result.reconciled is True
    deps["mark_reconciled"].assert_awaited_once_with("message-1")


@pytest.mark.asyncio
async def test_failed_projection_does_not_reconcile_marker():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(_state(OrchestrationStatus.COMPLETED))
    finalizer, deps = _finalizer(
        store,
        project_status=AsyncMock(return_value=False),
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        await finalizer.finalize(room_id="room-1", message_id="message-1")

    deps["mark_reconciled"].assert_not_awaited()


@pytest.mark.asyncio
async def test_no_run_marker_stays_pending_until_settlement_window():
    finalizer, deps = _finalizer(None)

    immediate = await finalizer.finalize(
        room_id="room-1",
        message_id="message-1",
        settle_no_run=False,
    )
    assert immediate.reconciled is False
    deps["mark_reconciled"].assert_not_awaited()

    settled = await finalizer.finalize(
        room_id="room-1",
        message_id="message-1",
        settle_no_run=True,
    )
    assert settled.reconciled is True
    deps["mark_reconciled"].assert_awaited_once_with("message-1")
