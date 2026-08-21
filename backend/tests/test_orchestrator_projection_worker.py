"""Focused tests for the production projection outbox worker and projectors."""

from __future__ import annotations

from datetime import timedelta

from pymongo.errors import DuplicateKeyError

from dal.orchestrator.projection import (
    MongoAppendEventProjector,
    MongoFinalMessageProjector,
    MongoTerminalRunStatusProjector,
)
from execution.orchestrator.in_memory import (
    InMemoryOrchestratorEventStore,
    InMemoryOrchestratorRunStore,
)
from execution.orchestrator.models import (
    AssistantMessage,
    TextPart,
)
from execution.orchestrator.projection import (
    ProjectionOutboxWorker,
    SettlingProjectionDriver,
)
from execution.orchestrator.settlement import (
    TerminalCommitRequest,
    TerminalDecisionFacts,
    commit_terminal_decision,
)

from ._orchestrator_helpers import NOW, make_run


def _terminal_run(
    *,
    run_id: str = "run-1",
    room_id: str = "room-1",
    final_message_id: str = "final-1",
):
    run = make_run()
    run = run.model_copy(
        update={
            "run_id": run_id,
            "session_id": room_id,
            "room_id": room_id,
            "client_request_id": f"request-{run_id}",
            "request": run.request.model_copy(
                update={
                    "room_epoch": 1,
                    "user_message_id": f"user-{run_id}",
                    "request_fingerprint": f"fingerprint-{run_id}",
                }
            ),
            "status": "finalizing",
            "transcript": [
                *run.transcript,
                AssistantMessage(
                    message_id=final_message_id,
                    content=[TextPart(text="final answer")],
                    tool_calls=[],
                    finish_reason="stop",
                    usage=None,
                    created_at=NOW,
                ),
            ],
            "proposed_final_message_id": final_message_id,
        }
    )
    committed = commit_terminal_decision(
        run,
        facts=TerminalDecisionFacts(final_message_id=final_message_id),
        request=TerminalCommitRequest(
            expected_state_version=run.state_version,
            command_id="complete",
            event_id="event-terminal",
            event_sequence=1,
            event_intent_id="intent-event",
            final_message_intent_id="intent-message",
            public_run_intent_id="intent-run",
            final_message_target=room_id,
            public_run_target=run_id,
            created_at=NOW,
        ),
    )
    return committed.run


async def _stored_terminal_run(*, store=None, **kwargs):
    store = store or InMemoryOrchestratorRunStore()
    run = _terminal_run(**kwargs)
    created = await store.create(run, command_id="create")
    assert created.outcome == "accepted"
    return store, run


async def test_worker_scans_claims_projects_and_completes_all_intents():
    store, run = await _stored_terminal_run()
    projected = []

    async def project(intent, current_run):
        projected.append((intent.kind, current_run.run_id))
        return "accepted"

    worker = ProjectionOutboxWorker(
        run_store=store,
        projectors={
            "append_orchestrator_event": project,
            "deliver_final_message": project,
            "project_terminal_run_status": project,
        },
        worker_id="worker",
    )
    assert await worker.run_once(due_at=NOW) == 3
    stored = await store.load(run.run_id)
    assert stored is not None
    assert {item.status for item in stored.projection_outbox} == {"completed"}
    assert stored.projection_state == "settled"
    assert {kind for kind, _ in projected} == {
        "append_orchestrator_event",
        "deliver_final_message",
        "project_terminal_run_status",
    }


async def test_event_append_replays_without_duplicate_events():
    store, run = await _stored_terminal_run()
    events = InMemoryOrchestratorEventStore()
    worker = ProjectionOutboxWorker(
        run_store=store,
        projectors={
            "append_orchestrator_event": MongoAppendEventProjector(events).project,
            "deliver_final_message": _noop_projector,
            "project_terminal_run_status": _noop_projector,
        },
        worker_id="worker",
    )
    assert await worker.run_once(due_at=NOW) == 3
    assert len(events.events[run.run_id]) == 1
    # Re-run after settlement is a no-op: the completed intent is never replayed.
    assert await worker.run_once(due_at=NOW) == 0
    assert len(events.events[run.run_id]) == 1


async def test_final_message_projector_dedupes_on_message_id():
    store, run = await _stored_terminal_run()
    stored = await store.load(run.run_id)
    intent = next(
        item
        for item in stored.projection_outbox
        if item.kind == "deliver_final_message"
    )
    messages = _FakeMessageCollection()
    projector = MongoFinalMessageProjector(messages)
    assert await projector.project(intent, stored) == "accepted"
    assert await projector.project(intent, stored) == "replayed"
    assert len(messages.documents) == 1


async def test_terminal_run_status_projector_updates_public_runs():
    store, run = await _stored_terminal_run()
    stored = await store.load(run.run_id)
    intent = next(
        item
        for item in stored.projection_outbox
        if item.kind == "project_terminal_run_status"
    )
    runs = _FakeRunsCollection()
    projector = MongoTerminalRunStatusProjector(runs)
    assert await projector.project(intent, stored) == "accepted"
    assert runs.documents[run.run_id]["state"] == "completed"
    assert await projector.project(intent, stored) == "replayed"


async def test_worker_blocks_poison_intent_after_bounded_attempts():
    store, run = await _stored_terminal_run()

    async def fail(intent, current_run):
        return "error"

    worker = ProjectionOutboxWorker(
        run_store=store,
        projectors={
            "append_orchestrator_event": fail,
            "deliver_final_message": _noop_projector,
            "project_terminal_run_status": _noop_projector,
        },
        worker_id="worker",
        max_attempts=2,
        backoff_base_seconds=1,
    )
    assert await worker.run_once(due_at=NOW) == 2
    stored = await store.load(run.run_id)
    event_intent = next(
        item
        for item in stored.projection_outbox
        if item.kind == "append_orchestrator_event"
    )
    assert event_intent.status == "pending"
    assert event_intent.attempt_count == 1
    assert event_intent.next_attempt_at is not None

    later = event_intent.next_attempt_at + timedelta(seconds=1)
    assert await worker.run_once(due_at=later) == 0
    stored = await store.load(run.run_id)
    event_intent = next(
        item
        for item in stored.projection_outbox
        if item.kind == "append_orchestrator_event"
    )
    assert event_intent.status == "blocked"
    assert event_intent.blocked_reason == "projection attempts exceeded"
    assert stored.projection_state == "blocked"


async def test_settlement_waits_for_required_intents():
    store, run = await _stored_terminal_run()
    driver = SettlingProjectionDriver(store)
    stored = await store.load(run.run_id)
    assert stored.projection_state == "pending"

    # Settling before the worker completes required intents is a no-op.
    assert (await driver.settle(run.run_id)).projection_state == "pending"

    # Complete all required intents, then settlement transitions once.
    current = await store.load(run.run_id)
    for intent in list(current.projection_outbox):
        if intent.status != "pending":
            continue
        claimed = await store.claim_projection_intent(
            run.run_id,
            intent.intent_id,
            expected_state_version=current.state_version,
            owner_id="worker",
            lease_expires_at=NOW + timedelta(seconds=30),
        )
        current = claimed.run
        completed = await store.complete_projection_intent(
            run.run_id,
            intent.intent_id,
            expected_state_version=current.state_version,
            owner_id="worker",
        )
        current = completed.run

    settled = await driver.settle(run.run_id)
    assert settled.projection_state == "settled"


async def test_worker_crash_replay_completes_partial_projection():
    store, run = await _stored_terminal_run()
    stored = await store.load(run.run_id)

    # Simulate a worker that completed the event intent before crashing.
    event_intent = next(
        item
        for item in stored.projection_outbox
        if item.kind == "append_orchestrator_event"
    )
    claimed = await store.claim_projection_intent(
        run.run_id,
        event_intent.intent_id,
        expected_state_version=stored.state_version,
        owner_id="crashed-worker",
        lease_expires_at=NOW + timedelta(seconds=60),
    )
    completed = await store.complete_projection_intent(
        run.run_id,
        event_intent.intent_id,
        expected_state_version=claimed.run.state_version,
        owner_id="crashed-worker",
    )
    assert completed.outcome == "accepted"

    projected = []

    async def project(intent, current_run):
        projected.append(intent.kind)
        return "accepted"

    worker = ProjectionOutboxWorker(
        run_store=store,
        projectors={
            "append_orchestrator_event": project,
            "deliver_final_message": project,
            "project_terminal_run_status": project,
        },
        worker_id="recovery-worker",
    )
    assert await worker.run_once(due_at=NOW) == 2
    final = await store.load(run.run_id)
    assert final.projection_state == "settled"
    assert "append_orchestrator_event" not in projected


async def _noop_projector(intent, run):
    del intent, run
    return "accepted"


class _FakeMessageCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    async def find_one(self, query):
        return self.documents.get(query["message_id"])

    async def insert_one(self, document):
        message_id = document["message_id"]
        if message_id in self.documents:
            raise DuplicateKeyError("message_id")
        self.documents[message_id] = document


class _FakeRunsCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    async def update_one(self, query, update, **kwargs):
        run_id = query["run_id"]
        existing = self.documents.get(run_id)
        if existing is not None and existing.get("state") in {
            "completed",
            "failed",
            "canceled",
        }:
            raise DuplicateKeyError("run_id")
        self.documents[run_id] = {**update.get("$set", {}), "run_id": run_id}
        return True

    async def find_one(self, query):
        return self.documents.get(query["run_id"])
