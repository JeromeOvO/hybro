from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from common.dto import DeliveryEmitStatus, ProcessingStatusEvent
from execution.events import emit_processing_status
from execution.run_lifecycle_outcome import RunLifecycleWriteOutcome
from execution.terminal_projection import TerminalProjectionFinalizer
from jobs.stale_task_checker import (
    StaleTaskChecker,
    StaleTerminalProjectionDeps,
)
from models.run import Run, RunEvent


def test_legacy_run_documents_remain_compatible_without_projection():
    run = Run.model_validate({"run_id": "run-1", "room_id": "room-1"})
    event = RunEvent.model_validate(
        {
            "event_id": "evt-1",
            "run_id": "run-1",
            "room_id": "room-1",
            "seq": 1,
            "type": "run_completed",
        }
    )

    assert run.terminal_projection is None
    assert event.terminal_projection is None


def _fact(*, completed: set[str] | None = None) -> dict:
    completed = completed or set()
    steps = {
        name: {"state": "completed" if name in completed else "pending"}
        for name in (
            "run_event_sse",
            "processing_sse",
            "system_task",
            "system_task_delivery",
            "completion_metadata",
            "turn_event",
        )
    }
    return {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "room_id": "room-1",
        "seq": 2,
        "type": "run_completed",
        "payload": {"error_code": None, "error_message": None},
        "terminal_projection": {
            "version": 1,
            "event_id": "evt-1",
            "canonical_status": "completed",
            "frontend_message_id": "msg-1",
            "lifecycle_message_id": "msg-1",
            "client_request_id": "request-1",
            "details": {"turn_completion_kind": "deterministic"},
            "system_message_id": "sys-msg-1",
            "system_task_status": "completed",
            "completion_kind": "deterministic",
            "turn_event_type": "turn_completed",
            "turn_event_payload": {"duration_ms": 0},
            "delivery_id": "terminal:evt-1:processing",
            "steps": steps,
        },
    }


class ProjectionLifecycle:
    def __init__(self, fact: dict) -> None:
        self.fact = deepcopy(fact)
        self.listed = 0

    async def list_incomplete_terminal_projections(self, _limit):
        self.listed += 1
        if any(
            value["state"] != "completed"
            for value in self.fact["terminal_projection"]["steps"].values()
        ):
            return [deepcopy(self.fact)]
        return []

    async def claim_terminal_projection_step(self, event_id, step):
        assert event_id == self.fact["event_id"]
        state = self.fact["terminal_projection"]["steps"][step]
        if state["state"] != "pending":
            return None
        claimed = deepcopy(self.fact)
        state["state"] = "running"
        state["attempts"] = int(state.get("attempts", 0)) + 1
        return f"token-{step}", claimed

    async def complete_terminal_projection_step(self, event_id, step, token):
        assert event_id == self.fact["event_id"]
        assert token == f"token-{step}"
        self.fact["terminal_projection"]["steps"][step]["state"] = "completed"
        return True

    async def release_terminal_projection_step(
        self,
        event_id,
        step,
        token,
        error,
        *,
        retryable=True,
        delay_seconds=1,
    ):
        assert event_id == self.fact["event_id"]
        assert token == f"token-{step}"
        assert isinstance(error, BaseException)
        assert delay_seconds >= 1
        self.fact["terminal_projection"]["steps"][step]["state"] = (
            "pending" if retryable else "blocked"
        )
        return True

    async def block_terminal_projection_step(self, event_id, step, error):
        assert event_id == self.fact["event_id"]
        assert isinstance(error, BaseException)
        current = self.fact["terminal_projection"]["steps"].get(step)
        self.fact["terminal_projection"]["steps"][step] = {
            **(current if isinstance(current, dict) else {}),
            "state": "blocked",
        }
        return True

    async def refresh_terminal_projection_schedule(self, event_id):
        assert event_id == self.fact["event_id"]
        steps = self.fact["terminal_projection"]["steps"].values()
        self.fact["terminal_projection"]["pending"] = any(
            step["state"] in {"pending", "running"} for step in steps
        )
        return True


class FlakyPublisher:
    def __init__(self) -> None:
        self.processing_attempts = 0
        self.events = []

    async def emit_checked(self, event):
        self.events.append(event)
        if isinstance(event, ProcessingStatusEvent):
            self.processing_attempts += 1
            if self.processing_attempts == 1:
                return DeliveryEmitStatus.FAILED
        return DeliveryEmitStatus.DELIVERED


class FlakyMessageStore:
    def __init__(self) -> None:
        self.system_attempts = 0
        self.completion_attempts = 0

    async def set_system_task_terminal_state(self, message_id, target):
        assert (message_id, target) == ("sys-msg-1", "completed")
        self.system_attempts += 1
        return "missing" if self.system_attempts == 1 else "updated"

    async def set_turn_completion_kind(self, message_id, completion_kind):
        assert (message_id, completion_kind) == ("msg-1", "deterministic")
        self.completion_attempts += 1
        return "updated"


@pytest.mark.asyncio
async def test_terminal_projection_failure_retry_and_duplicate_recovery_are_idempotent():
    lifecycle = ProjectionLifecycle(_fact())
    publisher = FlakyPublisher()
    messages = FlakyMessageStore()
    delivery = SimpleNamespace(send_task_update=AsyncMock())
    appender = SimpleNamespace(append=AsyncMock())
    head_healer = AsyncMock(return_value=True)
    finalizer = TerminalProjectionFinalizer(
        lifecycle=lifecycle,
        event_publisher=publisher,
        message_store=messages,
        delivery=delivery,
        run_event_enabled=lambda: True,
        turn_event_appender=lambda: appender,
        head_healer=head_healer,
    )

    assert not await finalizer.finalize(deepcopy(lifecycle.fact))
    steps = lifecycle.fact["terminal_projection"]["steps"]
    assert steps["run_event_sse"]["state"] == "completed"
    assert steps["processing_sse"]["state"] == "pending"
    assert steps["system_task"]["state"] == "pending"
    assert steps["completion_metadata"]["state"] == "completed"
    assert steps["turn_event"]["state"] == "completed"
    assert lifecycle.fact["type"] == "run_completed"

    assert await finalizer.recover_pending(limit=100) == 1
    head_healer.assert_awaited_once_with("msg-1")
    assert all(step["state"] == "completed" for step in steps.values())
    assert publisher.processing_attempts == 2
    delivery.send_task_update.assert_awaited_once_with(
        room_id="room-1",
        message_id="sys-msg-1",
        status="completed",
        delivery_id="terminal:evt-1:system-task",
        client_request_id="request-1",
    )
    appender.append.assert_awaited_once()
    assert await finalizer.recover_pending(limit=100) == 0
    assert messages.completion_attempts == 1


@pytest.mark.asyncio
async def test_crash_after_system_db_cas_replays_stable_task_delivery_once():
    lifecycle = ProjectionLifecycle(
        _fact(
            completed={
                "run_event_sse",
                "processing_sse",
                "system_task",
                "completion_metadata",
                "turn_event",
            }
        )
    )
    message_store = SimpleNamespace(
        set_system_task_terminal_state=AsyncMock(return_value="already")
    )
    delivery = SimpleNamespace(send_task_update=AsyncMock(return_value=True))
    finalizer = TerminalProjectionFinalizer(
        lifecycle=lifecycle,
        event_publisher=AsyncMock(),
        message_store=message_store,
        delivery=delivery,
        run_event_enabled=lambda: True,
    )

    assert await finalizer.finalize(deepcopy(lifecycle.fact))
    message_store.set_system_task_terminal_state.assert_not_awaited()
    delivery.send_task_update.assert_awaited_once_with(
        room_id="room-1",
        message_id="sys-msg-1",
        status="completed",
        delivery_id="terminal:evt-1:system-task",
        client_request_id="request-1",
    )

    assert await finalizer.finalize(deepcopy(lifecycle.fact))
    assert delivery.send_task_update.await_count == 1


@pytest.mark.asyncio
async def test_inflight_reservation_never_completes_projection_step():
    fact = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "room_id": "room-1",
        "seq": 2,
        "type": "run_failed",
        "payload": {},
        "terminal_projection": {
            "version": 1,
            "canonical_status": "failed",
            "frontend_message_id": "msg-1",
            "lifecycle_message_id": "msg-1",
            "delivery_id": "terminal:evt-1:processing",
            "steps": {"processing_sse": {"state": "pending"}},
        },
    }
    lifecycle = ProjectionLifecycle(fact)
    publisher = SimpleNamespace(
        emit_checked=AsyncMock(return_value=DeliveryEmitStatus.IN_FLIGHT)
    )
    finalizer = TerminalProjectionFinalizer(
        lifecycle=lifecycle,
        event_publisher=publisher,
        message_store=AsyncMock(),
        delivery=AsyncMock(),
        run_event_enabled=lambda: True,
    )

    assert not await finalizer.finalize(deepcopy(lifecycle.fact))
    assert (
        lifecycle.fact["terminal_projection"]["steps"]["processing_sse"]["state"]
        == "pending"
    )

    publisher.emit_checked.return_value = DeliveryEmitStatus.ALREADY_DELIVERED
    assert await finalizer.finalize(deepcopy(lifecycle.fact))


@pytest.mark.asyncio
async def test_system_cas_already_does_not_repeat_completed_delivery_step():
    lifecycle = ProjectionLifecycle(
        _fact(
            completed={
                "run_event_sse",
                "processing_sse",
                "system_task_delivery",
                "completion_metadata",
                "turn_event",
            }
        )
    )
    message_store = SimpleNamespace(
        set_system_task_terminal_state=AsyncMock(return_value="already")
    )
    delivery = SimpleNamespace(send_task_update=AsyncMock(return_value=True))
    finalizer = TerminalProjectionFinalizer(
        lifecycle=lifecycle,
        event_publisher=AsyncMock(),
        message_store=message_store,
        delivery=delivery,
        run_event_enabled=lambda: True,
    )

    assert await finalizer.finalize(deepcopy(lifecycle.fact))
    message_store.set_system_task_terminal_state.assert_awaited_once()
    delivery.send_task_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_opposing_system_terminal_winner_is_not_delivered():
    lifecycle = ProjectionLifecycle(
        _fact(
            completed={
                "run_event_sse",
                "processing_sse",
                "completion_metadata",
                "turn_event",
            }
        )
    )
    message_store = SimpleNamespace(
        set_system_task_terminal_state=AsyncMock(return_value="conflict")
    )
    delivery = SimpleNamespace(send_task_update=AsyncMock())
    finalizer = TerminalProjectionFinalizer(
        lifecycle=lifecycle,
        event_publisher=AsyncMock(),
        message_store=message_store,
        delivery=delivery,
        run_event_enabled=lambda: True,
    )

    assert not await finalizer.finalize(deepcopy(lifecycle.fact))
    assert (
        lifecycle.fact["terminal_projection"]["steps"]["system_task"]["state"]
        == "blocked"
    )
    delivery.send_task_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_system_task_backoff_becomes_blocked_without_starving_new_fact():
    poison = ProjectionLifecycle(
        _fact(
            completed={
                "run_event_sse",
                "processing_sse",
                "completion_metadata",
                "turn_event",
            }
        )
    )
    message_store = SimpleNamespace(
        set_system_task_terminal_state=AsyncMock(return_value="missing")
    )
    delivery = SimpleNamespace(send_task_update=AsyncMock(return_value=True))
    finalizer = TerminalProjectionFinalizer(
        lifecycle=poison,
        event_publisher=AsyncMock(),
        message_store=message_store,
        delivery=delivery,
        run_event_enabled=lambda: True,
    )

    for _ in range(3):
        assert not await finalizer.finalize(deepcopy(poison.fact))

    steps = poison.fact["terminal_projection"]["steps"]
    assert steps["system_task"]["state"] == "blocked"
    assert steps["system_task_delivery"]["state"] == "blocked"
    assert poison.fact["terminal_projection"]["pending"] is False
    delivery.send_task_update.assert_not_awaited()

    newer = ProjectionLifecycle(
        _fact(
            completed={
                "run_event_sse",
                "system_task",
                "system_task_delivery",
                "completion_metadata",
                "turn_event",
            }
        )
    )
    publisher = SimpleNamespace(emit=AsyncMock(return_value=True))
    newer_finalizer = TerminalProjectionFinalizer(
        lifecycle=newer,
        event_publisher=publisher,
        message_store=SimpleNamespace(),
        delivery=SimpleNamespace(),
        run_event_enabled=lambda: False,
    )
    assert await newer_finalizer.recover_pending(limit=1) == 1
    publisher.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_emit_false_stays_pending_then_retries_from_terminal_fact():
    lifecycle = ProjectionLifecycle(
        _fact(
            completed={
                "run_event_sse",
                "system_task",
                "system_task_delivery",
                "completion_metadata",
                "turn_event",
            }
        )
    )
    publisher = SimpleNamespace(emit=AsyncMock(side_effect=[False, True]))
    finalizer = TerminalProjectionFinalizer(
        lifecycle=lifecycle,
        event_publisher=publisher,
        message_store=SimpleNamespace(),
        delivery=SimpleNamespace(),
        run_event_enabled=lambda: False,
    )

    assert not await finalizer.finalize(deepcopy(lifecycle.fact))
    assert (
        lifecycle.fact["terminal_projection"]["steps"]["processing_sse"]["state"]
        == "pending"
    )
    assert await finalizer.recover_pending(limit=10) == 1
    assert publisher.emit.await_count == 2


@pytest.mark.asyncio
async def test_terminal_lifecycle_is_durable_before_any_sse_projection():
    order: list[str] = []
    payload = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "room_id": "room-1",
        "seq": 2,
        "type": "run_failed",
        "payload": {},
        "terminal_projection": _fact()["terminal_projection"],
    }

    class Lifecycle:
        async def write_processing_status(self, *_args, **kwargs):
            projection = kwargs["terminal_projection"]
            assert projection["canonical_status"] == "failed"
            assert "turn_event" not in projection["steps"]
            order.append("db")
            return RunLifecycleWriteOutcome.accepted(payload)

        async def finalize_terminal_projection(self, _payload):
            order.append("sse")

    await emit_processing_status(
        room_id="room-1",
        status="failed",
        message_id="msg-1",
        run_lifecycle=Lifecycle(),
        event_publisher=AsyncMock(),
        run_event_enabled=lambda: True,
        client_request_id_resolver=SimpleNamespace(
            resolve_client_request_id=AsyncMock(return_value="request-1")
        ),
        system_message_id="sys-msg-1",
    )

    assert order == ["db", "sse"]


@pytest.mark.asyncio
async def test_stale_recovery_closes_root_commit_descendant_cleanup_crash_window():
    fact = {
        "event_id": "evt-failed",
        "run_id": "user-1",
        "room_id": "room-1",
        "seq": 3,
        "type": "run_failed",
        "payload": {},
        "terminal_projection": {
            "version": 1,
            "event_id": "evt-failed",
            "canonical_status": "failed",
            "frontend_message_id": "user-1",
            "lifecycle_message_id": "user-1",
            "descendant_cleanup_root_id": "user-1",
            "steps": {"descendant_cleanup": {"state": "pending"}},
        },
    }
    lifecycle = ProjectionLifecycle(fact)
    message_store = SimpleNamespace(
        project_descendant_terminal_state=AsyncMock(return_value=["child-1", "child-2"])
    )
    delivery = SimpleNamespace(send_task_update=AsyncMock(return_value=True))
    finalizer = TerminalProjectionFinalizer(
        lifecycle=lifecycle,
        event_publisher=AsyncMock(),
        message_store=message_store,
        delivery=delivery,
        run_event_enabled=lambda: True,
    )

    # The root fact already exists; this is a fresh stale-recovery instance
    # after the original worker crashed before cleanup.
    assert await finalizer.recover_pending(limit=100) == 1
    message_store.project_descendant_terminal_state.assert_awaited_once_with(
        "user-1",
        event_id="evt-failed",
        target_state="failed",
        exclude_message_ids=None,
    )
    assert delivery.send_task_update.await_count == 2
    assert {
        call.kwargs["delivery_id"] for call in delivery.send_task_update.await_args_list
    } == {
        "terminal:evt-failed:child:child-1",
        "terminal:evt-failed:child:child-2",
    }
    assert (
        lifecycle.fact["terminal_projection"]["steps"]["descendant_cleanup"]["state"]
        == "completed"
    )


@pytest.mark.asyncio
async def test_combined_intent_excludes_system_task_from_descendant_delivery():
    fact = {
        "event_id": "evt-combined",
        "run_id": "user-1",
        "room_id": "room-1",
        "terminal_projection": {
            "version": 1,
            "canonical_status": "failed",
            "frontend_message_id": "user-1",
            "lifecycle_message_id": "user-1",
            "system_message_id": "sys-user-1",
            "system_task_status": "failed",
            "descendant_cleanup_root_id": "user-1",
            "steps": {
                "descendant_cleanup": {"state": "pending"},
                "system_task": {"state": "pending"},
                "system_task_delivery": {"state": "pending"},
            },
        },
    }
    lifecycle = ProjectionLifecycle(fact)
    message_store = SimpleNamespace(
        project_descendant_terminal_state=AsyncMock(return_value=["child-1"]),
        set_system_task_terminal_state=AsyncMock(return_value="updated"),
    )
    delivery = SimpleNamespace(send_task_update=AsyncMock(return_value=True))
    finalizer = TerminalProjectionFinalizer(
        lifecycle=lifecycle,
        event_publisher=AsyncMock(),
        message_store=message_store,
        delivery=delivery,
        run_event_enabled=lambda: True,
    )

    assert await finalizer.finalize(deepcopy(fact))
    message_store.project_descendant_terminal_state.assert_awaited_once_with(
        "user-1",
        event_id="evt-combined",
        target_state="failed",
        exclude_message_ids=["sys-user-1"],
    )
    system_calls = [
        call
        for call in delivery.send_task_update.await_args_list
        if call.kwargs["message_id"] == "sys-user-1"
    ]
    assert len(system_calls) == 1
    assert system_calls[0].kwargs["delivery_id"] == (
        "terminal:evt-combined:system-task"
    )


@pytest.mark.asyncio
async def test_descendant_delivery_retry_rebuilds_ids_after_db_cleanup_crash():
    fact = {
        "event_id": "evt-failed",
        "run_id": "user-1",
        "room_id": "room-1",
        "seq": 3,
        "type": "run_failed",
        "payload": {},
        "terminal_projection": {
            "version": 1,
            "event_id": "evt-failed",
            "canonical_status": "rate_limited",
            "frontend_message_id": "user-1",
            "lifecycle_message_id": "user-1",
            "client_request_id": "request-1",
            "descendant_cleanup_root_id": "user-1",
            "steps": {"descendant_cleanup": {"state": "pending"}},
        },
    }
    lifecycle = ProjectionLifecycle(fact)
    message_store = SimpleNamespace(
        project_descendant_terminal_state=AsyncMock(return_value=["child-1", "child-2"])
    )
    calls = []
    fail_child_2_once = True

    async def send_task_update(**kwargs):
        nonlocal fail_child_2_once
        calls.append(kwargs)
        if kwargs["message_id"] == "child-2" and fail_child_2_once:
            fail_child_2_once = False
            return False
        return True

    finalizer = TerminalProjectionFinalizer(
        lifecycle=lifecycle,
        event_publisher=AsyncMock(),
        message_store=message_store,
        delivery=SimpleNamespace(send_task_update=send_task_update),
        run_event_enabled=lambda: True,
    )

    assert not await finalizer.finalize(deepcopy(lifecycle.fact))
    assert await finalizer.finalize(deepcopy(lifecycle.fact))
    assert message_store.project_descendant_terminal_state.await_count == 2
    assert {call["status"] for call in calls} == {"failed"}
    assert {call["client_request_id"] for call in calls} == {"request-1"}
    for child_id in ("child-1", "child-2"):
        assert {
            call["delivery_id"] for call in calls if call["message_id"] == child_id
        } == {f"terminal:evt-failed:child:{child_id}"}


@pytest.mark.asyncio
async def test_recover_pending_isolates_one_poison_fact():
    lifecycle = SimpleNamespace(
        list_incomplete_terminal_projections=AsyncMock(
            return_value=[{"event_id": "bad"}, {"event_id": "good"}]
        )
    )
    finalizer = TerminalProjectionFinalizer(
        lifecycle=lifecycle,
        event_publisher=AsyncMock(),
        message_store=AsyncMock(),
        delivery=AsyncMock(),
        run_event_enabled=lambda: True,
    )
    finalizer.finalize = AsyncMock(side_effect=[RuntimeError("poison"), True])

    assert await finalizer.recover_pending(limit=100) == 1
    assert finalizer.finalize.await_count == 2


@pytest.mark.asyncio
async def test_unknown_and_malformed_steps_are_durably_blocked():
    fact = {
        "event_id": "evt-poison",
        "run_id": "run-1",
        "room_id": "room-1",
        "terminal_projection": {
            "version": 1,
            "steps": {
                "future_step": {"state": "pending"},
                "corrupt_step": "not-an-object",
            },
        },
    }
    lifecycle = ProjectionLifecycle(fact)
    finalizer = TerminalProjectionFinalizer(
        lifecycle=lifecycle,
        event_publisher=AsyncMock(),
        message_store=AsyncMock(),
        delivery=AsyncMock(),
        run_event_enabled=lambda: True,
    )

    assert await finalizer.finalize(deepcopy(fact))
    assert {
        name: value["state"]
        for name, value in lifecycle.fact["terminal_projection"]["steps"].items()
    } == {"future_step": "blocked", "corrupt_step": "blocked"}
    assert lifecycle.fact["terminal_projection"]["pending"] is False


@pytest.mark.asyncio
async def test_stale_checker_invokes_terminal_projection_recovery():
    checker = StaleTaskChecker()
    recover = AsyncMock(return_value=2)
    checker.set_terminal_projection_deps(
        StaleTerminalProjectionDeps(recover_pending=recover)
    )

    await checker._recover_terminal_projections()

    recover.assert_awaited_once_with(limit=100)
