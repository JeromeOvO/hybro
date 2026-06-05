from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from common.dto import (
    HubCancelCommand,
    HubDispatchCommand,
    HubReplyCommand,
    OfflineHubFailureCommand,
)
from common.protocols import (
    AgentCallCounter,
    HubAgentStatusReader,
    HubDispatchPolicy,
    HubDispatchPort,
    HubInternalResponseDispatcher,
    HubLivenessReader,
    HubManagement,
    OfflineHubFailurePort,
)
from common.utils.time import utcnow
from hub_runtime_bridge import HubFacade, HubRuntimeBridgeConfig, HubRuntimeBridgeDeps
from hub_runtime_bridge.config import config_from_settings
from hub_runtime_bridge.hub_response_journal import InMemoryHubResponseJournal
from hub_runtime_bridge.repository.mongo import HubMongoRepository
from hub_runtime_bridge.service.ownership_lease_maintainer import (
    OwnershipLeaseMaintainer,
)
from hub_runtime_bridge.task_ownership import (
    InMemoryHubTaskOwnershipStore,
    MongoHubTaskOwnershipStore,
)

ROOT = Path(__file__).resolve().parents[1]


def test_common_hub_protocol_shapes_are_split_between_async_liveness_and_sync_cache() -> None:
    assert inspect.iscoroutinefunction(HubLivenessReader.is_hub_online)
    assert not inspect.iscoroutinefunction(HubDispatchPort.is_hub_online)
    assert "command" in inspect.signature(HubDispatchPort.cancel_hub_task).parameters
    assert "command" in inspect.signature(HubDispatchPort.reply_to_hub_task).parameters
    assert inspect.signature(HubManagement.connect_hub_stream).return_annotation != inspect.Signature.empty
    assert inspect.iscoroutinefunction(HubDispatchPolicy.can_dispatch_to_hub)
    assert inspect.iscoroutinefunction(HubInternalResponseDispatcher.dispatch_hub_internal_response)
    assert inspect.iscoroutinefunction(OfflineHubFailurePort.mark_hub_message_failed)
    assert inspect.iscoroutinefunction(HubAgentStatusReader.count_hub_agents)
    assert inspect.iscoroutinefunction(AgentCallCounter.increment_agent_call_count)


def test_hub_command_dtos_carry_current_relay_metadata() -> None:
    dispatch = HubDispatchCommand(
        hub_id="hub",
        agent_id="agent",
        local_agent_id="local",
        room_id="room",
        user_message_id="user-msg",
        agent_message_id="agent-msg",
        payload={"role": "user"},
        task_id="relay-pending-1",
    )
    cancel = HubCancelCommand(
        hub_id="hub", agent_message_id="agent-msg", local_agent_id="local"
    )
    reply = HubReplyCommand(
        hub_id="hub",
        agent_message_id="agent-msg",
        local_agent_id="local",
        room_id="room",
        reply_text="ok",
    )
    failure = OfflineHubFailureCommand(room_id="room", agent_message_id="agent-msg")

    assert dispatch.task_id == "relay-pending-1"
    assert cancel.agent_message_id == "agent-msg"
    assert reply.reply_text == "ok"
    assert failure.error_text


@pytest.mark.asyncio
async def test_journal_stable_id_dedupes_but_legacy_ingest_does_not() -> None:
    journal = InMemoryHubResponseJournal()
    stable_one = await journal.create_or_get(
        {"stable_idempotency_key": "hub-response:h:t:1", "task_id": "t"}
    )
    stable_two = await journal.create_or_get(
        {"stable_idempotency_key": "hub-response:h:t:1", "task_id": "t"}
    )
    legacy_one = await journal.create_or_get({"task_id": "t"})
    legacy_two = await journal.create_or_get({"task_id": "t"})

    assert stable_one["journal_id"] == stable_two["journal_id"]
    assert legacy_one["journal_id"] != legacy_two["journal_id"]
    assert legacy_one["dedupe_mode"] == "none"


@pytest.mark.asyncio
async def test_journal_active_claim_blocks_same_worker_duplicate() -> None:
    journal = InMemoryHubResponseJournal()
    record = await journal.create_or_get({"task_id": "task-1"})

    assert await journal.claim_for_processing(record["journal_id"], "worker-1")
    assert await journal.claim_for_processing(record["journal_id"], "worker-1") is None


@pytest.mark.asyncio
async def test_journal_accepts_naive_claim_datetimes() -> None:
    journal = InMemoryHubResponseJournal()
    active = await journal.create_or_get({"task_id": "active"})
    replayable = await journal.create_or_get({"task_id": "replayable"})

    journal._records[active["journal_id"]]["claim_expires_at"] = datetime(
        2999, 1, 1
    )
    journal._records[replayable["journal_id"]]["claim_expires_at"] = datetime(
        2000, 1, 1
    )

    assert await journal.claim_for_processing(active["journal_id"], "worker-1") is None
    replayable_records = await journal.find_replayable()
    assert [record["journal_id"] for record in replayable_records] == [
        replayable["journal_id"]
    ]


@pytest.mark.asyncio
async def test_ownership_store_accepts_missing_hub_task_then_rejects_conflicting_aliases() -> None:
    store = InMemoryHubTaskOwnershipStore()
    first = await store.claim_or_refresh(
        {"agent_message_id": "m1", "local_task_id": "pending-1"},
        "worker-1",
        "lease-1",
    )
    second = await store.claim_or_refresh({"hub_task_id": "hub-task"}, "worker-1", "lease-2")

    assert first["owner_id"] == "worker-1"
    assert second["owner_id"] == "worker-1"
    with pytest.raises(ValueError):
        await store.claim_or_refresh(
            {"agent_message_id": "m1", "hub_task_id": "hub-task"},
            "worker-2",
            "lease-3",
        )


@pytest.mark.asyncio
async def test_ownership_store_rejects_active_lease_takeover() -> None:
    store = InMemoryHubTaskOwnershipStore()
    await store.claim_or_refresh({"agent_message_id": "m1"}, "worker-1", "lease-1")

    with pytest.raises(ValueError, match="held by another worker"):
        await store.claim_or_refresh({"agent_message_id": "m1"}, "worker-2", "lease-2")


@pytest.mark.asyncio
async def test_mongo_ownership_takeover_requires_atomic_expiry_match() -> None:
    class Result:
        matched_count = 0

    class Collection:
        async def find_one(self, query):
            return {
                "ownership_id": "own-1",
                "owner_id": "worker-1",
                "lease_expires_at": utcnow() - timedelta(seconds=1),
                "aliases": {"agent_message_id": "m1"},
            }

        async def update_one(self, query, update, **kwargs):
            assert query["ownership_id"] == "own-1"
            assert any(
                "lease_expires_at" in clause
                and "$lte" in clause["lease_expires_at"]
                for clause in query["$or"]
            )
            assert kwargs == {"upsert": False}
            return Result()

    class Mongo:
        def collection(self, name):
            assert name == "hub_task_ownership"
            return Collection()

    store = MongoHubTaskOwnershipStore(Mongo())

    with pytest.raises(ValueError, match="held by another worker"):
        await store.claim_or_refresh({"agent_message_id": "m1"}, "worker-2", "lease-2")


@pytest.mark.asyncio
async def test_mongo_ownership_accepts_naive_mongo_lease_datetimes() -> None:
    class Collection:
        async def find_one(self, query):
            return {
                "ownership_id": "own-1",
                "owner_id": "worker-1",
                "lease_expires_at": datetime(2026, 5, 21, 11, 0),
                "aliases": {"agent_message_id": "m1"},
            }

    class Mongo:
        def collection(self, name):
            assert name == "hub_task_ownership"
            return Collection()

    store = MongoHubTaskOwnershipStore(Mongo())

    assert await store.resolve_owner("m1") is None


@pytest.mark.asyncio
async def test_in_memory_ownership_accepts_naive_lease_datetimes() -> None:
    store = InMemoryHubTaskOwnershipStore()
    record = await store.claim_or_refresh({"agent_message_id": "m1"}, "worker-1")
    store._records[record["ownership_id"]]["lease_expires_at"] = datetime(
        2026, 5, 21, 11, 0
    )

    assert await store.resolve_owner("m1") is None


@pytest.mark.asyncio
async def test_mongo_ownership_first_claim_uses_upsert() -> None:
    class Result:
        matched_count = 0

    class Collection:
        async def find_one(self, query):
            return None

        async def update_one(self, query, update, **kwargs):
            assert "$or" not in query
            assert update["$set"]["owner_id"] == "worker-1"
            assert update["$set"]["aliases.agent_message_id"] == "m1"
            assert kwargs == {"upsert": True}
            return Result()

    class Mongo:
        def collection(self, name):
            assert name == "hub_task_ownership"
            return Collection()

    store = MongoHubTaskOwnershipStore(Mongo())
    record = await store.claim_or_refresh(
        {"agent_message_id": "m1"},
        "worker-1",
        "lease-1",
    )

    assert record["owner_id"] == "worker-1"


@pytest.mark.asyncio
async def test_ownership_lease_maintainer_renews_and_releases_tracked_aliases() -> None:
    class Store:
        def __init__(self) -> None:
            self.claims = []
            self.releases = []

        async def claim_or_refresh(self, aliases, owner_id, lease_token):
            self.claims.append((aliases, owner_id, lease_token))
            return {
                "aliases": aliases,
                "owner_id": owner_id,
                "lease_token": lease_token,
            }

        async def release(self, alias, owner_id=None):
            self.releases.append((alias, owner_id))

    store = Store()
    maintainer = OwnershipLeaseMaintainer(
        task_runner=lambda coro, **kwargs: None,
        ownership_store=store,
        worker_id="worker-1",
    )
    maintainer.track(
        {"agent_message_id": "m1", "local_task_id": "pending-1"},
        "lease-1",
    )

    await maintainer.renew_once()
    await maintainer.release("m1")
    await maintainer.renew_once()

    assert store.claims == [
        (
            {"agent_message_id": "m1", "local_task_id": "pending-1"},
            "worker-1",
            "lease-1",
        )
    ]
    assert store.releases == [("m1", "worker-1")]


def test_hub_runtime_bridge_deps_use_config_not_raw_settings() -> None:
    deps = HubRuntimeBridgeDeps(config=HubRuntimeBridgeConfig())
    assert deps.config.offline_queue_max > 0


def test_config_from_settings_reads_hub_heartbeat_ttl() -> None:
    class Settings:
        relay_hub_heartbeat_ttl = 123

    assert config_from_settings(Settings()).heartbeat_ttl_seconds == 123


def test_hub_facade_satisfies_core_runtime_protocols() -> None:
    facade = HubFacade()
    assert isinstance(facade, HubManagement)
    assert isinstance(facade, HubLivenessReader)


@pytest.mark.asyncio
async def test_hub_mongo_repository_supports_async_dal_find_results() -> None:
    class Collection:
        async def find(self, query, **kwargs):
            assert query == {"user_id": "owner-1"}
            return [{"hub_id": "hub-1", "user_id": "owner-1"}]

    class Mongo:
        def collection(self, name):
            assert name == "hubs"
            return Collection()

    repo = HubMongoRepository(Mongo())

    assert await repo.get_by_owner("owner-1") == [
        {"hub_id": "hub-1", "user_id": "owner-1"}
    ]


@pytest.mark.asyncio
async def test_hub_mongo_repository_treats_matched_noop_update_as_success() -> None:
    class Result:
        matched_count = 1
        modified_count = 0

    class Collection:
        async def update_one(self, query, update):
            assert query == {"hub_id": "hub-1", "connection_id": "conn-1"}
            assert update == {"$set": {"is_online": False}}
            return Result()

    class Mongo:
        def collection(self, name):
            assert name == "hubs"
            return Collection()

    repo = HubMongoRepository(Mongo())

    assert await repo.update_hub_status_if_current(
        "hub-1", "conn-1", is_online=False
    )


@pytest.mark.asyncio
async def test_hub_mongo_repository_applies_recovery_limit_in_to_list() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.length = None

        async def to_list(self, length=None):
            self.length = length
            return [{"hub_id": "hub-1"}]

    cursor = Cursor()

    class Collection:
        def find(self, query, projection=None):
            assert query == {"is_online": False}
            assert projection == {"hub_id": 1}
            return cursor

    class Mongo:
        def collection(self, name):
            assert name == "hubs"
            return Collection()

    repo = HubMongoRepository(Mongo())

    assert await repo.list_offline_hubs_for_recovery(5) == [{"hub_id": "hub-1"}]
    assert cursor.length == 5
