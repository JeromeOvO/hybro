from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from execution.orchestration.run_store import (
    InMemoryOrchestrationRunStore,
    MongoOrchestrationRunStore,
    OrchestrationStoreConflict,
)
from models.orchestration import (
    OrchestrationEventType,
    OrchestrationRunEvent,
    OrchestrationRunState,
    OrchestrationStatus,
)

BASE_TIME = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)


class FakeMongoCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def find_one(self, query: dict, **kwargs) -> dict | None:
        docs = [doc for doc in self.docs if self._matches(doc, query)]
        sort = kwargs.get("sort")
        if sort:
            docs = self._sort(docs, sort)
        return dict(docs[0]) if docs else None

    async def find(self, query: dict, **kwargs) -> list[dict]:
        docs = [doc for doc in self.docs if self._matches(doc, query)]
        sort = kwargs.get("sort")
        if sort:
            docs = self._sort(docs, sort)
        limit = kwargs.get("limit")
        if limit:
            docs = docs[:limit]
        return [dict(doc) for doc in docs]

    async def insert_one(self, document: dict) -> str:
        self.docs.append(dict(document))
        return document.get("run_id") or document.get("event_id") or "inserted"

    async def replace_one(self, query: dict, replacement: dict, **_kwargs) -> bool:
        for index, doc in enumerate(self.docs):
            if self._matches(doc, query):
                self.docs[index] = dict(replacement)
                return True
        return False

    @classmethod
    def _matches(cls, doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$nin" in expected and actual in expected["$nin"]:
                    return False
                if "$lte" in expected and not actual <= expected["$lte"]:
                    return False
                continue
            if actual != expected:
                return False
        return True

    @staticmethod
    def _sort(docs: list[dict], sort: list[tuple[str, int]]) -> list[dict]:
        sorted_docs = list(docs)
        for key, direction in reversed(sort):
            sorted_docs.sort(
                key=lambda doc: doc.get(key),
                reverse=direction < 0,
            )
        return sorted_docs


class FakeMongo:
    def __init__(self) -> None:
        self.collections: dict[str, FakeMongoCollection] = {}

    def collection(self, name: str) -> FakeMongoCollection:
        return self.collections.setdefault(name, FakeMongoCollection())


def _state(**overrides) -> OrchestrationRunState:
    values = {
        "run_id": "run-1",
        "room_id": "room-1",
        "user_message_id": "user-message-1",
        "goal": "Find a short list of vendor options",
        "candidate_agent_ids": ["researcher", "analyst"],
        "client_request_id": "client-1",
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }
    values.update(overrides)
    return OrchestrationRunState(**values)


@pytest.mark.asyncio
async def test_create_get_and_versioned_save_return_copies():
    store = InMemoryOrchestrationRunStore()
    initial = _state()

    created = await store.create_run(initial)

    assert created == initial
    assert created is not initial

    initial.goal = "mutated by caller"
    initial.candidate_agent_ids.append("late-agent")
    fetched = await store.get_run("run-1")

    assert fetched is not None
    assert fetched.goal == "Find a short list of vendor options"
    assert fetched.candidate_agent_ids == ["researcher", "analyst"]

    updated = fetched.model_copy(deep=True)
    updated.status = OrchestrationStatus.RUNNING
    updated.state_version = 1
    updated.updated_at = BASE_TIME + timedelta(seconds=5)

    saved = await store.save_state(updated, expected_version=0)

    assert saved == updated
    assert saved is not updated
    saved.status = OrchestrationStatus.FAILED
    saved.candidate_agent_ids.append("mutated-return")

    refetched = await store.get_run("run-1")
    assert refetched is not None
    assert refetched.status == OrchestrationStatus.RUNNING
    assert refetched.state_version == 1
    assert refetched.candidate_agent_ids == ["researcher", "analyst"]


@pytest.mark.asyncio
async def test_save_state_raises_conflict_when_expected_version_is_stale():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(_state())
    updated = _state(state_version=1, status=OrchestrationStatus.RUNNING)

    with pytest.raises(OrchestrationStoreConflict, match="state_version"):
        await store.save_state(updated, expected_version=1)

    stored = await store.get_run("run-1")
    assert stored is not None
    assert stored.state_version == 0
    assert stored.status == OrchestrationStatus.CREATED


@pytest.mark.asyncio
async def test_save_state_requires_version_to_advance_by_one():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(_state())

    non_advancing = _state(state_version=0, status=OrchestrationStatus.RUNNING)
    with pytest.raises(OrchestrationStoreConflict, match="advance"):
        await store.save_state(non_advancing, expected_version=0)

    first_update = _state(state_version=1, status=OrchestrationStatus.RUNNING)
    await store.save_state(first_update, expected_version=0)

    still_current = _state(state_version=1, status=OrchestrationStatus.DISPATCHING)
    with pytest.raises(OrchestrationStoreConflict, match="advance"):
        await store.save_state(still_current, expected_version=1)

    regressing = _state(state_version=0, status=OrchestrationStatus.DISPATCHING)
    with pytest.raises(OrchestrationStoreConflict, match="advance"):
        await store.save_state(regressing, expected_version=1)

    stored = await store.get_run("run-1")
    assert stored is not None
    assert stored.state_version == 1
    assert stored.status == OrchestrationStatus.RUNNING


@pytest.mark.asyncio
async def test_get_latest_by_user_message_id_uses_latest_created_run():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(_state(run_id="run-1", user_message_id="message-1"))
    await store.create_run(_state(run_id="run-2", user_message_id="message-2"))
    await store.create_run(_state(run_id="run-3", user_message_id="message-1"))

    latest = await store.get_latest_by_user_message_id("message-1")

    assert latest is not None
    assert latest.run_id == "run-3"
    assert await store.get_latest_by_user_message_id("missing") is None


@pytest.mark.asyncio
async def test_append_event_stores_event_copy():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(_state())
    event = OrchestrationRunEvent(
        run_id="run-1",
        room_id="room-1",
        type=OrchestrationEventType.RUN_CREATED,
        state_version=0,
        payload={"nested": {"value": "original"}},
        created_at=BASE_TIME,
    )

    appended = await store.append_event(event)

    assert appended == event
    assert appended is not event

    event.payload["nested"]["value"] = "mutated-input"
    appended.payload["nested"]["value"] = "mutated-return"

    stored_event = store._events_by_run["run-1"][0]
    assert stored_event.payload == {"nested": {"value": "original"}}
    assert stored_event is not event


@pytest.mark.asyncio
async def test_append_event_rejects_missing_run_duplicate_id_and_future_version():
    store = InMemoryOrchestrationRunStore()
    missing_run_event = OrchestrationRunEvent(
        event_id="event-missing-run",
        run_id="missing-run",
        room_id="room-1",
        type=OrchestrationEventType.RUN_CREATED,
        state_version=0,
    )

    with pytest.raises(KeyError, match="missing-run"):
        await store.append_event(missing_run_event)

    await store.create_run(_state())
    event = OrchestrationRunEvent(
        event_id="event-1",
        run_id="run-1",
        room_id="room-1",
        type=OrchestrationEventType.RUN_CREATED,
        state_version=0,
    )
    await store.append_event(event)

    duplicate = event.model_copy(deep=True)
    duplicate.type = OrchestrationEventType.STATE_REDUCED
    with pytest.raises(OrchestrationStoreConflict, match="event_id"):
        await store.append_event(duplicate)

    future_version = OrchestrationRunEvent(
        event_id="event-2",
        run_id="run-1",
        room_id="room-1",
        type=OrchestrationEventType.STATE_REDUCED,
        state_version=1,
    )
    with pytest.raises(OrchestrationStoreConflict, match="state_version"):
        await store.append_event(future_version)

    assert len(store._events_by_run["run-1"]) == 1


@pytest.mark.asyncio
async def test_mongo_store_persists_runs_across_store_instances_and_versions():
    mongo = FakeMongo()
    first_store = MongoOrchestrationRunStore(mongo)
    second_store = MongoOrchestrationRunStore(mongo)
    initial = _state()

    await first_store.create_run(initial)
    fetched = await second_store.get_run("run-1")

    assert fetched == initial
    assert fetched is not initial

    updated = fetched.model_copy(deep=True)
    updated.status = OrchestrationStatus.RUNNING
    updated.state_version = 1
    updated.updated_at = BASE_TIME + timedelta(seconds=5)
    await second_store.save_state(updated, expected_version=0)

    stale = fetched.model_copy(deep=True)
    stale.status = OrchestrationStatus.DISPATCHING
    stale.state_version = 1
    with pytest.raises(OrchestrationStoreConflict, match="state_version"):
        await first_store.save_state(stale, expected_version=0)

    event = OrchestrationRunEvent(
        event_id="event-1",
        run_id="run-1",
        room_id="room-1",
        type=OrchestrationEventType.STATE_REDUCED,
        state_version=1,
        created_at=BASE_TIME,
    )
    await second_store.append_event(event)

    assert mongo.collections["orchestration_runs"].docs[0]["status"] == "running"
    assert mongo.collections["orchestration_run_events"].docs[0]["event_id"] == (
        "event-1"
    )


@pytest.mark.asyncio
async def test_list_recoverable_filters_terminal_statuses_and_respects_limit():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(
        _state(
            run_id="completed",
            status=OrchestrationStatus.COMPLETED,
            updated_at=BASE_TIME - timedelta(minutes=10),
        )
    )
    await store.create_run(
        _state(
            run_id="oldest-running",
            status=OrchestrationStatus.RUNNING,
            updated_at=BASE_TIME - timedelta(minutes=5),
        )
    )
    await store.create_run(
        _state(
            run_id="failed",
            status=OrchestrationStatus.FAILED,
            updated_at=BASE_TIME - timedelta(minutes=4),
        )
    )
    await store.create_run(
        _state(
            run_id="newer-waiting",
            status=OrchestrationStatus.WAITING_AGENT,
            updated_at=BASE_TIME - timedelta(minutes=1),
        )
    )

    recoverable = await store.list_recoverable(limit=1)

    assert [state.run_id for state in recoverable] == ["oldest-running"]
    recoverable[0].status = OrchestrationStatus.CANCELED

    all_recoverable = await store.list_recoverable()
    assert [state.run_id for state in all_recoverable] == [
        "oldest-running",
        "newer-waiting",
    ]
    assert all_recoverable[0].status == OrchestrationStatus.RUNNING


@pytest.mark.asyncio
async def test_reconstruct_from_envelope_builds_schema_v2_state_without_trajectory():
    store = InMemoryOrchestrationRunStore()
    envelope = {
        "candidate_agent_ids": ["researcher", "analyst"],
        "client_request_id": "client-42",
        "supervisor_trajectory": {"status": "legacy-running"},
    }

    state = await store.reconstruct_from_envelope(
        run_id="run-42",
        room_id="room-7",
        user_message_id="message-9",
        envelope=envelope,
        goal="Summarize the latest account notes",
    )

    assert state.run_id == "run-42"
    assert state.room_id == "room-7"
    assert state.user_message_id == "message-9"
    assert state.goal == "Summarize the latest account notes"
    assert state.candidate_agent_ids == ["researcher", "analyst"]
    assert state.client_request_id == "client-42"
    assert state.schema_version == 2
    assert state.state_version == 0
    assert state.status == OrchestrationStatus.CREATED


@pytest.mark.asyncio
async def test_reconstruct_from_envelope_reads_room_agent_set_snapshots():
    store = InMemoryOrchestrationRunStore()

    flat_state = await store.reconstruct_from_envelope(
        run_id="run-flat",
        room_id="room-7",
        user_message_id="message-9",
        envelope={
            "room_agent_set": {
                "agent-a": "Analyst",
                "agent-b": "Researcher",
            },
            "client_request_id": "client-flat",
        },
        goal="Summarize the latest account notes",
    )
    nested_state = await store.reconstruct_from_envelope(
        run_id="run-nested",
        room_id="room-7",
        user_message_id="message-9",
        envelope={
            "room_config": {
                "room_agent_set": {
                    "agent-c": "Writer",
                    "agent-d": "Reviewer",
                }
            }
        },
        goal="Summarize the latest account notes",
    )

    assert flat_state.candidate_agent_ids == ["agent-a", "agent-b"]
    assert flat_state.client_request_id == "client-flat"
    assert nested_state.candidate_agent_ids == ["agent-c", "agent-d"]
