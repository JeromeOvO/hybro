from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pymongo.errors import AutoReconnect, ServerSelectionTimeoutError

from dal.orchestrator.run_store import MongoOrchestratorRunStore
from dal.orchestrator.stores import (
    MongoAgentCallLedgerStore,
    MongoObservationInboxStore,
    MongoRoomEpochStore,
)
from execution.orchestrator.a2a_runtime.errors import RecoverableAdapterError
from execution.orchestrator.a2a_runtime.models import (
    A2AObservationInboxRecord,
    NormalizedA2AObservation,
)

from ._orchestrator_a2a_helpers import ledger_record
from ._orchestrator_helpers import NOW


class Cursor:
    async def to_list(self, *, length=None):
        return []


class OperationalCollection:
    def __init__(self, *, replace_error=None, write_before_error=False):
        self.values = []
        self.replace_error = replace_error
        self.write_before_error = write_before_error

    async def find_one(self, query):
        return next(
            (deepcopy(value) for value in self.values if _matches(value, query)), None
        )

    async def insert_one(self, document):
        self.values.append(deepcopy(document))
        return SimpleNamespace(inserted_id=1)

    async def replace_one(self, query, document, *, upsert=False):
        matched = None
        for index, value in enumerate(self.values):
            if _matches(value, query):
                matched = index
                break
        if matched is not None and (
            self.replace_error is None or self.write_before_error
        ):
            self.values[matched] = deepcopy(document)
        elif (
            matched is None
            and upsert
            and (self.replace_error is None or self.write_before_error)
        ):
            self.values.append(deepcopy(document))
        if self.replace_error is not None:
            error = self.replace_error
            self.replace_error = None
            raise error
        return SimpleNamespace(
            modified_count=int(matched is not None),
            matched_count=int(matched is not None),
            upserted_id=1 if matched is None and upsert else None,
        )

    async def delete_many(self, query):
        before = len(self.values)
        self.values = [value for value in self.values if not _matches(value, query)]
        return SimpleNamespace(deleted_count=before - len(self.values))

    def find(self, query):
        return Cursor()


class DeactivationBarrierCollection(OperationalCollection):
    def __init__(self):
        super().__init__()
        self.reads = 0
        self.both_reading = asyncio.Event()
        self.release_reads = asyncio.Event()

    async def find_one(self, query):
        result = await super().find_one(query)
        if query.get("active") is True and query.get("room_id") == "room-1":
            self.reads += 1
            if self.reads == 2:
                self.both_reading.set()
            await self.release_reads.wait()
        return result


class ActivationBarrierCollection(OperationalCollection):
    def __init__(self):
        super().__init__()
        self.enabled = False
        self.reads = 0
        self.both_reading = asyncio.Event()
        self.release_reads = asyncio.Event()

    async def find_one(self, query):
        result = await super().find_one(query)
        if self.enabled and query == {"room_id": "room-activation-race"}:
            self.reads += 1
            if self.reads == 2:
                self.both_reading.set()
            if self.reads <= 2:
                await self.release_reads.wait()
        return result


class FindFailureCollection(OperationalCollection):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def find_one(self, query):
        raise self.error


def _matches(document, query):
    return all(document.get(key) == expected for key, expected in query.items())


def inbox_record():
    observation = NormalizedA2AObservation(
        observation_id="observation-1",
        call_record_id=ledger_record().call_record_id,
        source_kind="direct",
        source_identity="direct:1",
        binding_scope="endpoint",
        event_kind="working",
        observed_at=NOW,
    )
    return A2AObservationInboxRecord(
        observation_id=observation.observation_id,
        source_kind=observation.source_kind,
        source_identity=observation.source_identity,
        payload_digest="payload",
        received_at=NOW,
        binding_scope=observation.binding_scope,
        room_id="room-1",
        room_epoch=1,
        call_record_id=observation.call_record_id,
        event_kind=observation.event_kind,
        observation=observation,
    )


@pytest.mark.parametrize(
    "error",
    [AutoReconnect("reconnect"), ServerSelectionTimeoutError("selection")],
)
async def test_mongo_operational_errors_translate_without_swallowing(error):
    store = MongoAgentCallLedgerStore(FindFailureCollection(error))
    with pytest.raises(RecoverableAdapterError) as raised:
        await store.load("run-1", "call-1")
    assert isinstance(raised.value.__cause__, type(error))


async def test_generic_run_checkpoint_store_translates_operational_reads():
    store = MongoOrchestratorRunStore(
        FindFailureCollection(AutoReconnect("checkpoint read failed"))
    )
    with pytest.raises(RecoverableAdapterError):
        await store.load("run-1")


async def test_call_and_inbox_cas_acknowledgement_loss_reload_exact_winner():
    call_collection = OperationalCollection(
        replace_error=AutoReconnect("ack lost"), write_before_error=True
    )
    call_store = MongoAgentCallLedgerStore(call_collection)
    call = ledger_record()
    assert await call_store.insert(call) == "accepted"
    desired = call.model_copy(
        update={"state_version": 1, "next_attempt_at": NOW, "updated_at": NOW}
    )
    assert await call_store.cas(desired, expected_state_version=0) == "replayed"

    inbox_collection = OperationalCollection(
        replace_error=ServerSelectionTimeoutError("ack lost"),
        write_before_error=True,
    )
    inbox_store = MongoObservationInboxStore(inbox_collection)
    inbox = inbox_record()
    assert await inbox_store.insert(inbox) == "accepted"
    desired_inbox = inbox.model_copy(update={"state_version": 1, "last_error": "x"})
    assert await inbox_store.cas(desired_inbox, expected_state_version=0) == "replayed"


async def test_unclassifiable_call_cas_outage_remains_typed_retryable():
    collection = OperationalCollection(
        replace_error=AutoReconnect("write not classified"), write_before_error=False
    )
    store = MongoAgentCallLedgerStore(collection)
    call = ledger_record()
    assert await store.insert(call) == "accepted"
    desired = call.model_copy(
        update={"state_version": 1, "next_attempt_at": NOW, "updated_at": NOW}
    )
    with pytest.raises(RecoverableAdapterError):
        await store.cas(desired, expected_state_version=0)


async def test_forced_concurrent_activation_uses_stable_identity_not_timestamp():
    collection = ActivationBarrierCollection()
    store = MongoRoomEpochStore(collection)
    _, first = await store.activate(
        "room-activation-race", "creation-0", activated_at=NOW
    )
    await store.deactivate(
        "room-activation-race",
        first.epoch,
        "deletion-0",
        deactivated_at=NOW,
    )
    collection.enabled = True
    first_task = asyncio.create_task(
        store.activate("room-activation-race", "creation-1", activated_at=NOW)
    )
    second_task = asyncio.create_task(
        store.activate(
            "room-activation-race",
            "creation-1",
            activated_at=NOW + timedelta(seconds=1),
        )
    )
    await collection.both_reading.wait()
    collection.release_reads.set()
    outcomes = await asyncio.gather(first_task, second_task)
    assert sorted(outcome for outcome, _ in outcomes) == ["accepted", "replayed"]
    assert outcomes[0][1].updated_at == outcomes[1][1].updated_at


async def test_forced_concurrent_exact_deactivation_is_accepted_plus_replayed():
    collection = DeactivationBarrierCollection()
    store = MongoRoomEpochStore(collection)
    outcome, active = await store.activate("room-1", "create-1", activated_at=NOW)
    assert outcome == "accepted"
    first = asyncio.create_task(
        store.deactivate("room-1", active.epoch, "delete-1", deactivated_at=NOW)
    )
    second = asyncio.create_task(
        store.deactivate(
            "room-1",
            active.epoch,
            "delete-1",
            deactivated_at=NOW + timedelta(seconds=1),
        )
    )
    await collection.both_reading.wait()
    collection.release_reads.set()
    outcomes = await asyncio.gather(first, second)
    assert sorted(outcome for outcome, _ in outcomes) == ["accepted", "replayed"]


async def test_room_ack_loss_reloads_semantic_winner_and_unclassifiable_retries():
    activation_collection = OperationalCollection(
        replace_error=AutoReconnect("activation ack lost"), write_before_error=True
    )
    activation_store = MongoRoomEpochStore(activation_collection)
    outcome, active = await activation_store.activate(
        "room-activation-ack", "creation-1", activated_at=NOW
    )
    assert outcome == "replayed" and active.creation_id == "creation-1"

    missing_collection = OperationalCollection(
        replace_error=AutoReconnect("activation unclassified"),
        write_before_error=False,
    )
    with pytest.raises(RecoverableAdapterError):
        await MongoRoomEpochStore(missing_collection).activate(
            "room-missing", "creation-1", activated_at=NOW
        )


async def test_deactivation_ack_loss_reloads_exact_tombstone():
    collection = OperationalCollection()
    store = MongoRoomEpochStore(collection)
    _, active = await store.activate("room-1", "create-1", activated_at=NOW)
    collection.replace_error = AutoReconnect("deactivation ack lost")
    collection.write_before_error = True
    outcome, tombstone = await store.deactivate(
        "room-1", active.epoch, "delete-1", deactivated_at=NOW
    )
    assert outcome == "replayed"
    assert tombstone.active is False and tombstone.deletion_id == "delete-1"

    unclassified = OperationalCollection()
    retry_store = MongoRoomEpochStore(unclassified)
    _, retry_active = await retry_store.activate(
        "room-unclassified", "creation-1", activated_at=NOW
    )
    unclassified.replace_error = AutoReconnect("deactivation unclassified")
    unclassified.write_before_error = False
    with pytest.raises(RecoverableAdapterError):
        await retry_store.deactivate(
            "room-unclassified",
            retry_active.epoch,
            "deletion-1",
            deactivated_at=NOW,
        )
