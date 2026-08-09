import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from common.a2a_constants import SSEProcessingStatus
from execution.run_command_handler import RunCommandHandler
from execution.run_lifecycle import RunLifecycleAdapter
from execution.run_lifecycle_outcome import RunLifecycleWriteStatus
from models.run import RunState
from tests.test_orchestration_run_lifecycle_projection import (
    InMemoryRunEventRepository,
    InMemoryRunRepository,
)


def _processing_run() -> dict:
    return {
        "run_id": "msg-1",
        "room_id": "room-1",
        "trigger_message_id": "msg-1",
        "parent_message_id": "msg-1",
        "state": RunState.PROCESSING.value,
        "seq": 1,
    }


@pytest.mark.asyncio
async def test_checked_terminal_write_reports_cas_conflict():
    run = _processing_run()
    run["state"] = RunState.CANCELED.value
    repository = InMemoryRunRepository([run])
    handler = RunCommandHandler(
        run_repository=repository,
        run_event_repository=InMemoryRunEventRepository(),
    )

    outcome = await handler.write_processing_status(
        "room-1", SSEProcessingStatus.COMPLETED, "msg-1"
    )

    assert outcome.status == RunLifecycleWriteStatus.CONFLICT
    assert outcome.payload is None


@pytest.mark.asyncio
async def test_repeated_same_terminal_write_does_not_grow_sequence_or_events():
    run = _processing_run()
    run["state"] = RunState.CANCELED.value
    run["seq"] = 7
    repository = InMemoryRunRepository([run])
    events = InMemoryRunEventRepository()
    handler = RunCommandHandler(
        run_repository=repository,
        run_event_repository=events,
    )

    outcome = await handler.write_processing_status(
        "room-1", SSEProcessingStatus.CANCELED, "msg-1"
    )

    assert outcome.status == RunLifecycleWriteStatus.CONFLICT
    assert repository.docs["msg-1"]["seq"] == 7
    assert events.events == []


@pytest.mark.asyncio
async def test_same_terminal_replay_removes_legacy_projection_copy_from_run_head():
    run = _processing_run()
    run.update(
        {
            "state": RunState.COMPLETED.value,
            "seq": 2,
            "terminal_projection": {"canonical_status": "stale"},
        }
    )
    event = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "room_id": "room-1",
        "seq": 2,
        "type": "run_completed",
        "payload": {},
        "terminal_projection": {
            "version": 1,
            "canonical_status": "completed",
            "frontend_message_id": "msg-1",
            "lifecycle_message_id": "msg-1",
            "steps": {},
        },
    }
    repository = InMemoryRunRepository([run])
    handler = RunCommandHandler(
        run_repository=repository,
        run_event_repository=InMemoryRunEventRepository([event]),
    )

    outcome = await handler.write_processing_status(
        "room-1", SSEProcessingStatus.COMPLETED, "msg-1"
    )

    assert outcome.status == RunLifecycleWriteStatus.REPLAYED
    assert "terminal_projection" not in repository.docs["msg-1"]


@pytest.mark.asyncio
async def test_legacy_rejected_terminal_replay_does_not_infer_projection_intent():
    run = _processing_run()
    run.update({"state": RunState.FAILED.value, "seq": 2})
    event = {
        "event_id": "evt-rejected",
        "run_id": "msg-1",
        "room_id": "room-1",
        "seq": 2,
        "type": "run_failed",
        "payload": {"error_code": "REJECTED", "error_message": "denied"},
    }
    events = InMemoryRunEventRepository([event])
    handler = RunCommandHandler(
        run_repository=InMemoryRunRepository([run]),
        run_event_repository=events,
    )
    inferred = {
        "version": 1,
        "canonical_status": "rejected",
        "frontend_message_id": "msg-1",
        "lifecycle_message_id": "msg-1",
        "system_message_id": "sys-msg-1",
        "system_task_status": "failed",
        "steps": {"system_task": {"state": "pending"}},
    }

    outcome = await handler.write_processing_status(
        "room-1",
        SSEProcessingStatus.REJECTED,
        "msg-1",
        terminal_projection=inferred,
    )

    assert outcome.status == RunLifecycleWriteStatus.REPLAYED
    assert "terminal_projection" not in outcome.payload
    assert "terminal_projection" not in events.events[0]


@pytest.mark.asyncio
async def test_same_terminal_replay_repairs_projection_without_opposing_delivery():
    repository = InMemoryRunRepository([_processing_run()])
    events = InMemoryRunEventRepository()
    handler = RunCommandHandler(
        run_repository=repository,
        run_event_repository=events,
    )
    projection = {
        "version": 1,
        "canonical_status": "completed",
        "frontend_message_id": "msg-1",
        "lifecycle_message_id": "msg-1",
        "steps": {
            "run_event_sse": {"state": "pending"},
            "processing_sse": {"state": "pending"},
        },
    }

    first = await handler.write_processing_status(
        "room-1",
        SSEProcessingStatus.COMPLETED,
        "msg-1",
        terminal_projection=projection,
    )
    replay = await handler.write_processing_status(
        "room-1",
        SSEProcessingStatus.COMPLETED,
        "msg-1",
        terminal_projection=projection,
    )
    opposing = await handler.write_processing_status(
        "room-1",
        SSEProcessingStatus.CANCELED,
        "msg-1",
        terminal_projection={**projection, "canonical_status": "canceled"},
    )

    assert first.status == RunLifecycleWriteStatus.ACCEPTED
    assert replay.status == RunLifecycleWriteStatus.REPLAYED
    assert replay.payload["event_id"] == first.payload["event_id"]
    assert opposing.status == RunLifecycleWriteStatus.CONFLICT
    assert len(events.events) == 1


@pytest.mark.asyncio
async def test_same_terminal_replay_atomically_enriches_missing_intent_and_steps():
    repository = InMemoryRunRepository([_processing_run()])
    events = InMemoryRunEventRepository()
    handler = RunCommandHandler(
        run_repository=repository,
        run_event_repository=events,
    )
    sparse = {
        "version": 1,
        "canonical_status": "completed",
        "frontend_message_id": "msg-1",
        "lifecycle_message_id": "msg-1",
        "steps": {"processing_sse": {"state": "pending"}},
    }
    first = await handler.write_processing_status(
        "room-1",
        SSEProcessingStatus.COMPLETED,
        "msg-1",
        terminal_projection=sparse,
    )
    event = events.events[-1]
    event["terminal_projection"]["steps"]["processing_sse"].update(
        {
            "state": "completed",
            "claim_token": None,
            "completed_at": "2026-01-01T00:00:00Z",
        }
    )
    richer = {
        **sparse,
        "system_message_id": "sys-msg-1",
        "system_task_status": "completed",
        "steps": {
            "processing_sse": {"state": "pending"},
            "system_task": {"state": "pending"},
            "system_task_delivery": {"state": "pending"},
        },
    }

    replay = await handler.write_processing_status(
        "room-1",
        SSEProcessingStatus.COMPLETED,
        "msg-1",
        terminal_projection=richer,
    )

    assert first.status == RunLifecycleWriteStatus.ACCEPTED
    assert replay.status == RunLifecycleWriteStatus.REPLAYED
    projection = replay.payload["terminal_projection"]
    assert projection["canonical_status"] == "completed"
    assert projection["system_message_id"] == "sys-msg-1"
    assert projection["steps"]["processing_sse"]["state"] == "completed"
    assert projection["steps"]["processing_sse"]["completed_at"]
    assert projection["steps"]["system_task"]["state"] == "pending"
    assert projection["pending"] is True
    assert "terminal_projection" not in repository.docs["msg-1"]


@pytest.mark.asyncio
async def test_concurrent_same_terminal_replays_merge_disjoint_steps_monotonically():
    repository = InMemoryRunRepository([_processing_run()])

    class AtomicEvents(InMemoryRunEventRepository):
        def __init__(self):
            super().__init__()
            self.lock = asyncio.Lock()

        async def find_one_and_update(self, query, update, **kwargs):
            async with self.lock:
                await asyncio.sleep(0)
                return await super().find_one_and_update(query, update, **kwargs)

    events = AtomicEvents()
    first_handler = RunCommandHandler(
        run_repository=repository,
        run_event_repository=events,
    )
    second_handler = RunCommandHandler(
        run_repository=repository,
        run_event_repository=events,
    )
    sparse = {
        "version": 1,
        "canonical_status": "completed",
        "frontend_message_id": "msg-1",
        "lifecycle_message_id": "msg-1",
        "steps": {"processing_sse": {"state": "pending"}},
    }
    await first_handler.write_processing_status(
        "room-1",
        SSEProcessingStatus.COMPLETED,
        "msg-1",
        terminal_projection=sparse,
    )
    processing = events.events[-1]["terminal_projection"]["steps"]["processing_sse"]
    processing.update(
        {
            "state": "running",
            "claim_token": "owner-1",
            "claim_expires_at": "2026-01-01T00:05:00Z",
        }
    )
    system_intent = {
        **sparse,
        "system_message_id": "sys-msg-1",
        "system_task_status": "completed",
        "steps": {
            **sparse["steps"],
            "system_task": {"state": "pending"},
            "system_task_delivery": {"state": "pending"},
        },
    }
    completion_intent = {
        **sparse,
        "completion_kind": "deterministic",
        "steps": {
            **sparse["steps"],
            "completion_metadata": {"state": "pending"},
        },
    }

    outcomes = await asyncio.gather(
        first_handler.write_processing_status(
            "room-1",
            SSEProcessingStatus.COMPLETED,
            "msg-1",
            terminal_projection=system_intent,
        ),
        second_handler.write_processing_status(
            "room-1",
            SSEProcessingStatus.COMPLETED,
            "msg-1",
            terminal_projection=completion_intent,
        ),
    )

    assert {outcome.status for outcome in outcomes} == {
        RunLifecycleWriteStatus.REPLAYED
    }
    projection = events.events[-1]["terminal_projection"]
    assert projection["canonical_status"] == "completed"
    assert projection["steps"]["processing_sse"]["state"] == "running"
    assert projection["steps"]["processing_sse"]["claim_token"] == "owner-1"
    assert set(projection["steps"]) == {
        "processing_sse",
        "system_task",
        "system_task_delivery",
        "completion_metadata",
    }


@pytest.mark.asyncio
async def test_projected_and_watchdog_terminal_facts_include_delivery_recovery_intent():
    for source in ("projection", "watchdog"):
        repository = InMemoryRunRepository([deepcopy(_processing_run())])
        events = InMemoryRunEventRepository()
        handler = RunCommandHandler(
            run_repository=repository,
            run_event_repository=events,
        )
        if source == "projection":
            payload = await handler.project_run_state(
                room_id="room-1",
                run_id="msg-1",
                trigger_message_id="msg-1",
                target_state=RunState.CANCELED,
                terminal_reason="request canceled",
                causation_id="cancel-fact",
            )
            expected_status = "canceled"
        else:
            payload = await handler.append_run_timeout_failure(
                "room-1", "msg-1", stale_minutes=5
            )
            expected_status = "failed"

        projection = payload["terminal_projection"]
        assert projection["canonical_status"] == expected_status
        assert projection["pending"] is True
        assert set(projection["steps"]) == {
            "descendant_cleanup",
            "run_event_sse",
            "processing_sse",
        }
        assert projection["descendant_cleanup_root_id"] == "msg-1"
        assert projection["delivery_id"].endswith(":processing")


@pytest.mark.asyncio
async def test_lifecycle_adapter_immediately_finalizes_projected_and_watchdog_facts():
    payload = {
        "event_id": "evt-1",
        "terminal_projection": {"version": 1},
    }
    command = AsyncMock()
    command.project_run_state.return_value = payload
    command.append_run_timeout_failure.return_value = payload
    adapter = RunLifecycleAdapter(command, AsyncMock())
    finalizer = AsyncMock()
    adapter.bind_terminal_finalizer(finalizer)

    assert (
        await adapter.project_run_state(
            room_id="room-1",
            run_id="msg-1",
            trigger_message_id="msg-1",
            target_state=RunState.CANCELED,
            terminal_reason="request canceled",
            causation_id="cancel-1",
        )
        == payload
    )
    assert (
        await adapter.append_run_timeout_failure("room-1", "msg-2", stale_minutes=5)
        == payload
    )

    assert finalizer.finalize.await_count == 2


@pytest.mark.asyncio
async def test_projection_recovery_query_only_selects_due_pending_facts():
    events = AsyncMock()
    events.find.return_value = []
    handler = RunCommandHandler(
        run_repository=AsyncMock(),
        run_event_repository=events,
    )

    assert await handler.list_incomplete_terminal_projections(limit=7) == []

    query = events.find.await_args.args[0]
    assert query["terminal_projection.version"] == 1
    assert query["terminal_projection.pending"] is True
    assert "$lte" in query["terminal_projection.next_attempt_at"]
    assert events.find.await_args.kwargs["sort"] == [
        ("terminal_projection.next_attempt_at", 1),
        ("ts", 1),
    ]
    assert events.find.await_args.kwargs["limit"] == 7


@pytest.mark.asyncio
async def test_projection_claim_accepts_initial_pending_step_with_null_schedule():
    events = AsyncMock()
    events.find_one_and_update.return_value = None
    handler = RunCommandHandler(
        run_repository=AsyncMock(),
        run_event_repository=events,
    )

    assert (
        await handler.claim_terminal_projection_step("evt-1", "processing_sse") is None
    )

    query = events.find_one_and_update.await_args.args[0]
    pending_branch = query["$or"][0]
    due_conditions = pending_branch["$or"]
    assert {
        "terminal_projection.steps.processing_sse.next_attempt_at": None
    } in due_conditions


@pytest.mark.asyncio
async def test_projection_schedule_skips_malformed_steps_without_raising():
    events = InMemoryRunEventRepository(
        [
            {
                "event_id": "evt-1",
                "terminal_projection": {"version": 1, "steps": "corrupt"},
            }
        ]
    )
    handler = RunCommandHandler(
        run_repository=AsyncMock(),
        run_event_repository=events,
    )

    assert await handler.refresh_terminal_projection_schedule("evt-1")
    schedule = events.events[0]["terminal_projection"]
    assert schedule["pending"] is False
    assert schedule["next_attempt_at"] is None


@pytest.mark.asyncio
async def test_projection_refresh_includes_step_merged_during_crash_window():
    event = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "room_id": "room-1",
        "seq": 2,
        "type": "run_completed",
        "payload": {},
        "terminal_projection": {
            "version": 1,
            "canonical_status": "completed",
            "frontend_message_id": "msg-1",
            "lifecycle_message_id": "msg-1",
            "pending": True,
            "steps": {
                "processing_sse": {"state": "completed"},
            },
        },
    }

    class MergeBeforeRefreshEvents(InMemoryRunEventRepository):
        async def find_one(self, *args, **kwargs):
            raise AssertionError("schedule refresh must not read before atomic update")

        async def find_one_and_update(self, query, update, **kwargs):
            fields = update[0].get("$set", {}) if isinstance(update, list) else {}
            pending = fields.get("terminal_projection.pending")
            if isinstance(pending, dict) and "$let" in pending:
                # A different instance committed richer intent and crashed just
                # before this instance atomically refreshes the schedule.
                self.events[0]["terminal_projection"]["steps"]["system_task"] = {
                    "state": "pending",
                    "next_attempt_at": "2030-01-01T00:00:00Z",
                }
            return await super().find_one_and_update(query, update, **kwargs)

    events = MergeBeforeRefreshEvents([event])
    handler = RunCommandHandler(
        run_repository=InMemoryRunRepository(),
        run_event_repository=events,
    )

    assert await handler.refresh_terminal_projection_schedule("evt-1")
    projection = events.events[0]["terminal_projection"]
    assert projection["pending"] is True
    assert projection["next_attempt_at"] == "2030-01-01T00:00:00Z"
    assert projection["steps"]["system_task"]["state"] == "pending"


@pytest.mark.asyncio
async def test_projection_release_persists_backoff_or_terminal_blocked_state():
    events = AsyncMock()
    events.update_one.return_value = True
    handler = RunCommandHandler(
        run_repository=AsyncMock(),
        run_event_repository=events,
    )

    assert await handler.release_terminal_projection_step(
        "evt-1",
        "processing_sse",
        "token-1",
        RuntimeError("transport unavailable"),
        retryable=True,
        delay_seconds=8,
    )
    retry_update = events.update_one.await_args.args[1]["$set"]
    assert retry_update["terminal_projection.steps.processing_sse.state"] == "pending"
    assert (
        retry_update["terminal_projection.steps.processing_sse.next_attempt_at"]
        is not None
    )

    assert await handler.release_terminal_projection_step(
        "evt-1",
        "system_task",
        "token-2",
        RuntimeError("opposing winner"),
        retryable=False,
    )
    blocked_update = events.update_one.await_args.args[1]["$set"]
    assert blocked_update["terminal_projection.steps.system_task.state"] == "blocked"
    assert (
        blocked_update["terminal_projection.steps.system_task.next_attempt_at"] is None
    )
    assert blocked_update["terminal_projection.steps.system_task.blocked_at"]


@pytest.mark.asyncio
async def test_checked_terminal_write_reports_mongo_event_error_without_secret():
    class FailingEvents(InMemoryRunEventRepository):
        async def insert_one(self, document):
            del document
            raise RuntimeError("PRIVATE_PROMPT token=SECRET_TOKEN")

    handler = RunCommandHandler(
        run_repository=InMemoryRunRepository([_processing_run()]),
        run_event_repository=FailingEvents(),
    )

    outcome = await handler.write_processing_status(
        "room-1", SSEProcessingStatus.COMPLETED, "msg-1"
    )

    assert outcome.status == RunLifecycleWriteStatus.ERROR
    assert outcome.error_class == "RuntimeError"
    assert outcome.error_message_size_bytes
    assert outcome.error_fingerprint
    assert "PRIVATE_PROMPT" not in repr(outcome)
    assert "SECRET_TOKEN" not in repr(outcome)


@pytest.mark.asyncio
async def test_event_append_repairs_head_after_first_projection_failure():
    class FailHeadOnce(InMemoryRunRepository):
        def __init__(self, docs):
            super().__init__(docs)
            self.failures = 1

        async def update_one(self, query, update):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("transient head failure")
            return await super().update_one(query, update)

    repository = FailHeadOnce([deepcopy(_processing_run())])
    events = InMemoryRunEventRepository()
    handler = RunCommandHandler(
        run_repository=repository,
        run_event_repository=events,
    )

    outcome = await handler.write_processing_status(
        "room-1", SSEProcessingStatus.COMPLETED, "msg-1"
    )

    assert outcome.status == RunLifecycleWriteStatus.ACCEPTED
    assert outcome.payload is not None
    assert repository.docs["msg-1"]["state"] == RunState.COMPLETED.value
    assert repository.docs["msg-1"]["seq"] == 2
    assert len(events.events) == 1


@pytest.mark.asyncio
async def test_event_append_and_failed_repair_reports_error_for_retry():
    class AlwaysFailHead(InMemoryRunRepository):
        async def update_one(self, query, update):
            del query, update
            raise RuntimeError("head unavailable")

    events = InMemoryRunEventRepository()
    handler = RunCommandHandler(
        run_repository=AlwaysFailHead([_processing_run()]),
        run_event_repository=events,
    )

    outcome = await handler.write_processing_status(
        "room-1", SSEProcessingStatus.COMPLETED, "msg-1"
    )

    assert outcome.status == RunLifecycleWriteStatus.ERROR
    assert len(events.events) == 1
