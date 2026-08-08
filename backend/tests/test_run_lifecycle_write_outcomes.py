from copy import deepcopy

import pytest

from common.a2a_constants import SSEProcessingStatus
from execution.run_command_handler import RunCommandHandler
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
