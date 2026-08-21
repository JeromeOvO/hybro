from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from dal.orchestrator_v3.run_store import MongoOrchestratorRunStore
from dal.orchestrator_v3.stores import (
    MongoObservationConflictStore,
    MongoRoomEpochStore,
)
from execution.orchestrator.a2a_runtime.models import A2AObservationConflictRecord

from ._orchestrator_v3_helpers import NOW, make_run


class LiveDeactivationBarrier:
    def __init__(self, collection):
        self.collection = collection
        self.reads = 0
        self.both_reading = asyncio.Event()
        self.release_reads = asyncio.Event()

    async def find_one(self, query):
        result = await self.collection.find_one(query)
        if query.get("active") is True and query.get("room_id") == "room-race":
            self.reads += 1
            if self.reads == 2:
                self.both_reading.set()
            await self.release_reads.wait()
        return result

    async def insert_one(self, document):
        return await self.collection.insert_one(document)

    async def replace_one(self, query, document, *, upsert=False):
        return await self.collection.replace_one(query, document, upsert=upsert)

    async def delete_many(self, query):
        return await self.collection.delete_many(query)

    def find(self, query):
        return self.collection.find(query)


class LiveActivationBarrier(LiveDeactivationBarrier):
    async def find_one(self, query):
        result = await self.collection.find_one(query)
        if query == {"room_id": "room-activation-race"}:
            self.reads += 1
            if self.reads == 2:
                self.both_reading.set()
            if self.reads <= 2:
                await self.release_reads.wait()
        return result


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("HYBRO_TEST_LIVE_MONGO") != "1",
        reason="set HYBRO_TEST_LIVE_MONGO=1 to run live Mongo parity",
    ),
]


async def test_live_mongo_run_due_dates_and_order_match_contract():
    client = AsyncIOMotorClient(
        os.getenv(
            "HYBRO_TEST_MONGO_URI",
            "mongodb://localhost:27017/?directConnection=true",
        ),
        serverSelectionTimeoutMS=3000,
    )
    database_name = f"hybro_run_store_test_{uuid.uuid4().hex}"
    database = client[database_name]
    try:
        collection = database["runs"]
        store = MongoOrchestratorRunStore(collection)
        base = make_run()

        def scheduled(run_id, *, next_attempt_at, updated_at):
            return base.model_copy(
                update={
                    "run_id": run_id,
                    "session_id": f"session-{run_id}",
                    "room_id": f"room-{run_id}",
                    "client_request_id": f"request-{run_id}",
                    "request": base.request.model_copy(
                        update={"request_fingerprint": f"fingerprint-{run_id}"}
                    ),
                    "recovery_claim": base.recovery_claim.model_copy(
                        update={"next_attempt_at": next_attempt_at}
                    ),
                    "updated_at": updated_at,
                }
            )

        runs = [
            scheduled(
                "later",
                next_attempt_at=NOW - timedelta(seconds=5),
                updated_at=NOW,
            ),
            scheduled(
                "earlier",
                next_attempt_at=None,
                updated_at=NOW - timedelta(seconds=20),
            ),
        ]
        for run in runs:
            assert (
                await store.create(run, command_id=f"create:{run.run_id}")
            ).outcome == "accepted"

        raw = await collection.find_one({"run_id": "later"})
        due = await store.list_due_runs(due_at=NOW, limit=2)

        assert raw["recovery_claim"]["next_attempt_at"].tzinfo is None
        assert [run.run_id for run in due] == ["earlier", "later"]
        assert all(run.updated_at.tzinfo is not None for run in due)
        assert due[1].recovery_claim.next_attempt_at.tzinfo is not None
    finally:
        await client.drop_database(database_name)
        client.close()


async def test_live_mongo_concurrent_exact_replays_are_classified():
    client = AsyncIOMotorClient(
        os.getenv(
            "HYBRO_TEST_MONGO_URI",
            "mongodb://localhost:27017/?directConnection=true",
        ),
        serverSelectionTimeoutMS=3000,
    )
    database_name = f"hybro_plan3_test_{uuid.uuid4().hex}"
    database = client[database_name]
    try:
        conflicts = database["conflicts"]
        epochs = database["epochs"]
        await conflicts.create_index("conflict_id", unique=True)
        await epochs.create_index("room_id", unique=True)
        conflict_store = MongoObservationConflictStore(conflicts)
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
        conflict_outcomes = await asyncio.gather(
            conflict_store.insert(conflict), conflict_store.insert(conflict)
        )
        assert sorted(conflict_outcomes) == ["accepted", "replayed"]

        epoch_store = MongoRoomEpochStore(epochs)
        epoch_outcomes = await asyncio.gather(
            epoch_store.activate("room-1", "creation-1", activated_at=NOW),
            epoch_store.activate("room-1", "creation-1", activated_at=NOW),
        )
        assert sorted(outcome for outcome, _ in epoch_outcomes) == [
            "accepted",
            "replayed",
        ]

        _, prior = await epoch_store.activate(
            "room-activation-race", "creation-0", activated_at=NOW
        )
        await epoch_store.deactivate(
            "room-activation-race",
            prior.epoch,
            "deletion-0",
            deactivated_at=NOW,
        )
        activation_barrier = LiveActivationBarrier(epochs)
        activation_store = MongoRoomEpochStore(activation_barrier)
        activation_first = asyncio.create_task(
            activation_store.activate(
                "room-activation-race", "creation-1", activated_at=NOW
            )
        )
        activation_second = asyncio.create_task(
            activation_store.activate(
                "room-activation-race",
                "creation-1",
                activated_at=NOW + timedelta(seconds=1),
            )
        )
        await activation_barrier.both_reading.wait()
        activation_barrier.release_reads.set()
        activation_outcomes = await asyncio.gather(activation_first, activation_second)
        assert sorted(outcome for outcome, _ in activation_outcomes) == [
            "accepted",
            "replayed",
        ]
        assert activation_outcomes[0][1].updated_at == (
            activation_outcomes[1][1].updated_at
        )

        barrier = LiveDeactivationBarrier(epochs)
        race_store = MongoRoomEpochStore(barrier)
        _, active = await race_store.activate(
            "room-race", "creation-race", activated_at=NOW
        )
        first = asyncio.create_task(
            race_store.deactivate(
                "room-race", active.epoch, "deletion-race", deactivated_at=NOW
            )
        )
        second = asyncio.create_task(
            race_store.deactivate(
                "room-race",
                active.epoch,
                "deletion-race",
                deactivated_at=NOW + timedelta(seconds=1),
            )
        )
        await barrier.both_reading.wait()
        barrier.release_reads.set()
        deactivate_outcomes = await asyncio.gather(first, second)
        assert sorted(outcome for outcome, _ in deactivate_outcomes) == [
            "accepted",
            "replayed",
        ]
        assert deactivate_outcomes[0][1].updated_at == (
            deactivate_outcomes[1][1].updated_at
        )
    finally:
        await client.drop_database(database_name)
        client.close()
