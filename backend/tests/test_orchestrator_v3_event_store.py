"""Contract parity for the durable orchestrator Run event stores."""

from __future__ import annotations

from copy import deepcopy

from pymongo.errors import DuplicateKeyError

from dal.orchestrator_v3.event_store import MongoOrchestratorEventStore
from execution.orchestrator.in_memory import InMemoryOrchestratorEventStore
from execution.orchestrator.models import OrchestratorEvent

from ._orchestrator_v3_helpers import NOW
from .test_orchestrator_v3_a2a_mongo_parity import FakeCollection


def event(event_id: str, sequence: int, state_version: int = 1) -> OrchestratorEvent:
    return OrchestratorEvent(
        event_id=event_id,
        event_type="turn_started",
        session_id="room-1",
        run_id="run-1",
        sequence=sequence,
        state_version=state_version,
        causation_id=f"cause-{event_id}",
        payload={"status": "running"},
        created_at=NOW,
    )


class StaleReadDuplicateCollection(FakeCollection):
    """A concurrent winner is visible only through the post-race re-read."""

    def __init__(self, winner: dict[str, object]) -> None:
        super().__init__()
        self.winner = winner

    def find(self, query):
        del query
        return CursorView([])

    async def insert_one(self, document):
        del document
        raise DuplicateKeyError("duplicate key after concurrent winner")

    async def find_one(self, query):
        if query.get("event_id") == self.winner["event_id"]:
            return deepcopy(self.winner)
        return None


class CursorView:
    def __init__(self, values):
        self.values = values

    async def to_list(self, *, length=None):
        del length
        return deepcopy(self.values)


async def test_memory_and_mongo_append_read_and_ordering_match():
    stores = [
        InMemoryOrchestratorEventStore(),
        MongoOrchestratorEventStore(FakeCollection()),
    ]
    for store in stores:
        assert await store.append(event("event-1", 1)) == "accepted"
        assert await store.append(event("event-2", 2)) == "accepted"
        assert [item.event_id for item in await store.read("run-1")] == [
            "event-1",
            "event-2",
        ]
        assert [
            item.event_id for item in await store.read("run-1", after_sequence=1)
        ] == ["event-2"]
        assert await store.read("run-other") == []


async def test_memory_and_mongo_reject_identity_and_ordering_conflicts():
    stores = [
        InMemoryOrchestratorEventStore(),
        MongoOrchestratorEventStore(FakeCollection()),
    ]
    for store in stores:
        first = event("event-1", 1)
        assert await store.append(first) == "accepted"
        assert await store.append(first) == "replayed"
        assert await store.append(event("event-1", 1, state_version=2)) == "conflict"
        assert await store.append(event("event-2", 1)) == "conflict"
        assert await store.append(event("event-2", 3)) == "conflict"
        assert await store.append(event("event-2", 1, state_version=0)) == "conflict"
        assert [item.event_id for item in await store.read("run-1")] == ["event-1"]


async def test_mongo_duplicate_key_race_replays_the_exact_winner():
    collection = FakeCollection()
    store = MongoOrchestratorEventStore(collection)
    first = event("event-1", 1)
    assert await store.append(first) == "accepted"
    # A concurrent append lost the unique-index race but re-reads the exact
    # winner it tried to write.
    collection.insert_one = _duplicate_key_once_then_write(collection)
    assert await store.append(first) == "replayed"
    assert [item.event_id for item in await store.read("run-1")] == ["event-1"]


def _duplicate_key_once_then_write(collection: FakeCollection):
    original = collection.insert_one
    failed = False

    async def insert_one(document):
        nonlocal failed
        if not failed:
            failed = True
            raise DuplicateKeyError("duplicate key after concurrent winner")
        return await original(document)

    return insert_one


async def test_mongo_duplicate_key_race_classifies_divergent_winner_as_conflict():
    winner = event("event-1", 1, state_version=2)
    store = MongoOrchestratorEventStore(
        StaleReadDuplicateCollection(winner.model_dump(mode="json"))
    )
    # The pre-write read is stale, the insert loses the unique-index race, and
    # the re-read reveals a divergent occupant: conflict, never overwrite.
    assert await store.append(event("event-1", 1)) == "conflict"
