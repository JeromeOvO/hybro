from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from dal.orchestrator_v3.run_store import MongoOrchestratorRunStore
from dal.orchestrator_v3.stores import (
    MongoAgentCallLedgerStore,
    MongoObservationConflictStore,
    MongoObservationInboxStore,
    MongoRoomEpochStore,
)
from execution.orchestrator.a2a_runtime.in_memory import (
    InMemoryAgentCallLedgerStore,
    InMemoryObservationConflictStore,
    InMemoryObservationInboxStore,
    InMemoryRoomEpochStore,
)
from execution.orchestrator.a2a_runtime.models import (
    A2AObservationConflictRecord,
    A2AObservationInboxRecord,
    A2ARuntimePolicy,
    NormalizedA2AObservation,
)

from ._orchestrator_v3_a2a_helpers import ledger_record
from ._orchestrator_v3_helpers import NOW, make_run


class Cursor:
    def __init__(self, values):
        self.values = values

    async def to_list(self, *, length=None):
        return deepcopy(self.values if length is None else self.values[:length])


class FakeCollection:
    def __init__(self):
        self.values = []

    async def find_one(self, query):
        return next(
            (deepcopy(item) for item in self.values if _matches(item, query)), None
        )

    async def insert_one(self, document):
        self.values.append(deepcopy(document))
        return SimpleNamespace(inserted_id=len(self.values))

    async def replace_one(self, query, document, *, upsert=False):
        for index, item in enumerate(self.values):
            if _matches(item, query):
                modified = item != document
                self.values[index] = deepcopy(document)
                return SimpleNamespace(
                    modified_count=int(modified), matched_count=1, upserted_id=None
                )
        if upsert:
            self.values.append(deepcopy(document))
            return SimpleNamespace(modified_count=0, matched_count=0, upserted_id=1)
        return SimpleNamespace(modified_count=0, matched_count=0, upserted_id=None)

    async def delete_many(self, query):
        before = len(self.values)
        self.values = [item for item in self.values if not _matches(item, query)]
        return SimpleNamespace(deleted_count=before - len(self.values))

    def find(self, query):
        return Cursor([deepcopy(item) for item in self.values if _matches(item, query)])


class DuplicateAfterWriteCollection(FakeCollection):
    def __init__(self, *, fail_insert=False, fail_upsert=False):
        super().__init__()
        self.fail_insert = fail_insert
        self.fail_upsert = fail_upsert

    async def insert_one(self, document):
        result = await super().insert_one(document)
        if self.fail_insert:
            self.fail_insert = False
            raise DuplicateKeyError("duplicate key after concurrent winner")
        return result

    async def replace_one(self, query, document, *, upsert=False):
        result = await super().replace_one(query, document, upsert=upsert)
        if upsert and self.fail_upsert:
            self.fail_upsert = False
            raise DuplicateKeyError("duplicate key after concurrent upsert winner")
        return result


def _matches(document, query):  # noqa: C901
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(document, item) for item in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(document, item) for item in expected):
                return False
            continue
        actual = _get(document, key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected:
                actual_values = actual if isinstance(actual, list) else [actual]
                if not set(actual_values).intersection(expected["$in"]):
                    return False
            if "$exists" in expected and (actual is not None) != expected["$exists"]:
                return False
            if "$nin" in expected and actual in expected["$nin"]:
                return False
            if "$lte" in expected and actual is not None and actual > expected["$lte"]:
                return False
            continue
        if actual != expected:
            return False
    return True


def _get(document, dotted):
    value = document
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _inbox_record():
    observation = NormalizedA2AObservation(
        observation_id="observation-1",
        source_kind="webhook",
        source_identity="source-1",
        binding_scope="endpoint",
        event_kind="working",
        observed_at=NOW,
    )
    return A2AObservationInboxRecord(
        observation_id=observation.observation_id,
        source_kind=observation.source_kind,
        source_identity=observation.source_identity,
        payload_digest="digest",
        received_at=NOW,
        binding_scope=observation.binding_scope,
        room_id="room-1",
        room_epoch=1,
        event_kind=observation.event_kind,
        observation=observation,
    )


async def test_mongo_run_create_exact_retry_replays_persisted_candidate():
    store = MongoOrchestratorRunStore(FakeCollection())
    run = make_run()

    accepted = await store.create(run, command_id="create:run-1")
    replayed = await store.create(run, command_id="create:run-1")

    assert accepted.outcome == "accepted"
    assert replayed.outcome == "replayed"
    assert replayed.run == accepted.run
    assert replayed.run.processed_command_ids == ["create:run-1"]


async def test_mongo_run_duplicate_request_ignores_mongo_id():
    collection = FakeCollection()
    store = MongoOrchestratorRunStore(collection)
    run = make_run()
    assert (await store.create(run, command_id="create:run-1")).outcome == "accepted"
    collection.values[0]["_id"] = "mongo-generated-id"
    duplicate = run.model_copy(update={"run_id": "run-duplicate"})

    replayed = await store.create(duplicate, command_id="create:run-duplicate")

    assert replayed.outcome == "replayed"
    assert replayed.run.run_id == run.run_id


async def test_mongo_run_cas_does_not_duplicate_preapplied_command_id():
    store = MongoOrchestratorRunStore(FakeCollection())
    created = await store.create(make_run(), command_id="create:run-1")
    terminal = created.run.model_copy(
        update={
            "status": "failed",
            "terminal_reason": "test failure",
            "processed_command_ids": [
                *created.run.processed_command_ids,
                "complete:run-1",
            ],
            "state_version": created.run.state_version + 1,
        }
    )

    committed = await store.cas_mutate(
        terminal,
        expected_state_version=created.run.state_version,
        command_id="complete:run-1",
    )

    assert committed.outcome == "accepted"
    assert committed.run.processed_command_ids == ["create:run-1", "complete:run-1"]
    assert await store.load(terminal.run_id) == committed.run


async def test_mongo_and_memory_call_lease_contracts_match():
    stores = [
        InMemoryAgentCallLedgerStore(),
        MongoAgentCallLedgerStore(FakeCollection()),
    ]
    for store in stores:
        record = ledger_record()
        assert await store.insert(record) == "accepted"
        assert (
            await store.claim(
                record.call_record_id,
                expected_state_version=0,
                owner_id="owner",
                lease_expires_at=NOW,
                claimed_at=NOW,
            )
            is None
        )
        claimed = await store.claim(
            record.call_record_id,
            expected_state_version=0,
            owner_id="owner",
            lease_expires_at=NOW + timedelta(seconds=10),
            claimed_at=NOW,
        )
        assert claimed is not None
        assert (
            await store.renew(
                record.call_record_id,
                expected_state_version=claimed.state_version,
                owner_id="owner",
                lease_expires_at=NOW + timedelta(seconds=5),
                renewed_at=NOW + timedelta(seconds=1),
            )
            is None
        )
        renewed = await store.renew(
            record.call_record_id,
            expected_state_version=claimed.state_version,
            owner_id="owner",
            lease_expires_at=NOW + timedelta(seconds=20),
            renewed_at=NOW + timedelta(seconds=1),
        )
        assert renewed is not None
        released = await store.release(
            record.call_record_id,
            expected_state_version=renewed.state_version,
            owner_id="owner",
            next_attempt_at=NOW + timedelta(seconds=30),
            released_at=NOW + timedelta(seconds=2),
        )
        assert released is not None


async def test_mongo_and_memory_inbox_claim_takeover_and_stale_fence_match():
    stores = [
        InMemoryObservationInboxStore(),
        MongoObservationInboxStore(FakeCollection()),
    ]
    for store in stores:
        record = _inbox_record()
        assert await store.insert(record) == "accepted"
        first = await store.claim(
            record.observation_id,
            expected_state_version=0,
            owner_id="owner-a",
            claim_token="token-a",
            lease_expires_at=NOW + timedelta(seconds=1),
            claimed_at=NOW,
        )
        second = await store.claim(
            record.observation_id,
            expected_state_version=first.state_version,
            owner_id="owner-b",
            claim_token="token-b",
            lease_expires_at=NOW + timedelta(seconds=5),
            claimed_at=NOW + timedelta(seconds=2),
        )
        assert second is not None
        stale = first.model_copy(
            update={"state": "completed", "state_version": first.state_version + 1}
        )
        assert (
            await store.cas(
                stale,
                expected_state_version=first.state_version,
                owner_id="owner-a",
                claim_token="token-a",
            )
            == "conflict"
        )


async def test_memory_and_mongo_boundaries_defensively_copy_nested_evidence():
    ledger_stores = [
        InMemoryAgentCallLedgerStore(),
        MongoAgentCallLedgerStore(FakeCollection()),
    ]
    for store in ledger_stores:
        record = ledger_record()
        await store.insert(record)
        loaded = await store.load_by_record_id(record.call_record_id)
        loaded.recent_observation_ids.append("mutated-without-cas")
        loaded.runtime_policy.__dict__["max_transport_attempts"] = 999
        persisted = await store.load_by_record_id(record.call_record_id)
        assert persisted.recent_observation_ids == []
        assert persisted.runtime_policy.max_transport_attempts == 3

    inbox_stores = [
        InMemoryObservationInboxStore(),
        MongoObservationInboxStore(FakeCollection()),
    ]
    for store in inbox_stores:
        record = _inbox_record()
        await store.insert(record)
        loaded = await store.load(record.observation_id)
        loaded.observation.content.append({"kind": "text", "text": "mutated"})
        assert (await store.load(record.observation_id)).observation.content == []

    policy = A2ARuntimePolicy()
    with pytest.raises(ValidationError):
        policy.max_transport_attempts = 999


async def test_inbox_and_conflict_retention_is_directly_room_epoch_queryable():
    for inbox_store, conflict_store in [
        (InMemoryObservationInboxStore(), InMemoryObservationConflictStore()),
        (
            MongoObservationInboxStore(FakeCollection()),
            MongoObservationConflictStore(FakeCollection()),
        ),
    ]:
        inbox = _inbox_record().model_copy(
            update={"room_id": "room-1", "room_epoch": 1}
        )
        conflict = A2AObservationConflictRecord(
            conflict_id="conflict-retention",
            room_id="room-1",
            room_epoch=1,
            source_identity="source-retention",
            accepted_observation_id=inbox.observation_id,
            accepted_payload_digest="accepted",
            conflicting_payload_digest="conflicting",
            binding_scope="endpoint",
            received_at=NOW,
        )
        await inbox_store.insert(inbox)
        await conflict_store.insert(conflict)
        assert await inbox_store.delete_by_epoch("room-1", 1) == 1
        assert await conflict_store.delete_by_epoch("room-1", 1) == 1
        assert await inbox_store.load(inbox.observation_id) is None
        assert await conflict_store.list_for_source("source-retention") == []


async def test_mongo_duplicate_key_races_classify_exact_winners_as_replay():
    conflict_collection = DuplicateAfterWriteCollection(fail_insert=True)
    conflict_store = MongoObservationConflictStore(conflict_collection)
    conflict = A2AObservationConflictRecord(
        conflict_id="conflict-1",
        room_id="room-1",
        room_epoch=1,
        source_identity="source-1",
        accepted_observation_id="observation-1",
        accepted_payload_digest="accepted",
        conflicting_payload_digest="conflicting",
        binding_scope="endpoint",
        received_at=NOW,
    )
    assert await conflict_store.insert(conflict) == "replayed"
    assert await conflict_store.insert(conflict) == "replayed"

    epoch_collection = DuplicateAfterWriteCollection(fail_upsert=True)
    epoch_store = MongoRoomEpochStore(epoch_collection)
    outcome, epoch = await epoch_store.activate(
        "room-1", "creation-1", activated_at=NOW
    )
    assert outcome == "replayed"
    assert epoch.active and epoch.creation_id == "creation-1"
    assert (await epoch_store.activate("room-1", "creation-1", activated_at=NOW))[
        0
    ] == ("replayed")


async def test_mongo_and_memory_room_epoch_recreation_rules_match():
    stores = [InMemoryRoomEpochStore(), MongoRoomEpochStore(FakeCollection())]
    for store in stores:
        outcome, active = await store.activate("room-1", "create-1", activated_at=NOW)
        assert outcome == "accepted"
        assert (
            await store.deactivate(
                "room-1", active.epoch, "delete-1", deactivated_at=NOW
            )
        )[0] == "accepted"
        assert (await store.activate("room-1", "create-1", activated_at=NOW))[0] == (
            "conflict"
        )
        outcome, recreated = await store.activate(
            "room-1", "create-2", activated_at=NOW
        )
        assert outcome == "accepted"
        assert recreated.epoch == active.epoch + 1
