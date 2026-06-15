from __future__ import annotations

import asyncio

import pytest

from common.dto import HubAgentCounts, HubDispatchCommand
from hub_runtime_bridge import HubFacade, HubRuntimeBridgeConfig, HubRuntimeBridgeDeps
from hub_runtime_bridge.hub_response_journal import (
    InMemoryHubResponseJournal,
    MongoHubResponseJournal,
)
from hub_runtime_bridge.internal_response_router import HubInternalResponseRouter
from hub_runtime_bridge.task_ownership import MongoHubTaskOwnershipStore


class Repo:
    def __init__(self) -> None:
        self.hubs = {}

    async def upsert(self, hub_id, data):
        self.hubs[hub_id] = data

    async def get_by_id(self, hub_id):
        return self.hubs.get(hub_id)

    async def get_by_owner(self, owner_id):
        return [hub for hub in self.hubs.values() if hub.get("user_id") == owner_id]

    async def update_hub_status(self, hub_id, **fields):
        self.hubs.setdefault(hub_id, {"hub_id": hub_id, "user_id": "owner"}).update(
            fields
        )


class Counts:
    async def count_hub_agents(self, hub_id):
        return HubAgentCounts(active=2, inactive=1)


class OfflineFailures:
    def __init__(self) -> None:
        self.commands = []

    async def mark_hub_message_failed(self, command):
        self.commands.append(command)


class Sink:
    def __init__(self) -> None:
        self.events = []

    async def handle_hub_agent_response(self, event):
        self.events.append(event)


class _Streams:
    def __init__(self) -> None:
        self.heartbeats: list[str] = []
        self.alive: set[str] = set()

    async def record_heartbeat(self, hub_id: str) -> None:
        self.heartbeats.append(hub_id)
        self.alive.add(hub_id)

    async def is_hub_alive(self, hub_id: str) -> bool:
        return hub_id in self.alive


class _Writer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list, bool]] = []

    async def sync_hub_agents(
        self,
        hub_id: str,
        owner_id: str,
        descriptors: list,
        *,
        prune_missing: bool = True,
    ) -> list:
        self.calls.append((hub_id, owner_id, descriptors, prune_missing))
        return []


class _OwnershipStore:
    async def ensure_indexes(self) -> None:
        return None

    async def claim_or_refresh(self, *args, **kwargs):
        return {
            "aliases": kwargs.get("aliases", []),
            "lease_token": kwargs.get("lease_token"),
        }

    async def resolve_owner(self, *args, **kwargs):
        return None

    async def release(self, *args, **kwargs):
        return None


class _RepositoryForConnectionLiveness:
    async def get_by_id(self, hub_id: str) -> dict | None:
        return {"hub_id": hub_id, "user_id": "owner-1", "is_online": False}

    async def get_by_owner(self, owner_id: str) -> list[dict]:
        return [{"hub_id": "hub-1", "user_id": owner_id, "is_online": False}]


class CollectionStore:
    def __init__(self) -> None:
        self.collections = {}

    def collection(self, name):
        self.collections.setdefault(name, [])
        return self

    async def find_one(self, query):
        return None

    async def insert_one(self, document):
        return "id"

    async def update_one(self, query, update, **kwargs):
        return True

    async def find(self, query, **kwargs):
        return []

    async def create_index(self, *args, **kwargs):
        return "idx"


@pytest.mark.asyncio
async def test_facade_registration_status_and_stream_shape() -> None:
    repo = Repo()
    facade = HubFacade(
        HubRuntimeBridgeDeps(
            config=HubRuntimeBridgeConfig(),
            hub_repository=repo,
            hub_agent_status_reader=Counts(),
        )
    )
    hub = await facade.register_hub("hub-1", "owner-1")
    stream = facade.connect_hub_stream("hub-1")
    first = await anext(stream)
    status = await facade.hub_status_for_user("owner-1")

    assert hub.hub_id == "hub-1"
    assert first == {"type": "connection_ready"}
    assert status[0].agent_count == 3
    await stream.aclose()


def test_facade_uses_mongo_response_journal_when_mongo_is_provided() -> None:
    facade = HubFacade(mongo=CollectionStore())

    assert isinstance(facade.deps.hub_response_journal, MongoHubResponseJournal)
    assert isinstance(facade.deps.task_ownership_store, MongoHubTaskOwnershipStore)


@pytest.mark.asyncio
async def test_facade_dispatch_uses_internal_only_task_id() -> None:
    facade = HubFacade()
    command = HubDispatchCommand(
        hub_id="hub-1",
        agent_id="agent-1",
        local_agent_id="local-1",
        room_id="room-1",
        user_message_id="user-1",
        agent_message_id="agent-msg-1",
        payload={"body": "hello"},
        task_id="relay-pending-agent",
    )
    result = await facade.send_to_hub(command)

    assert result.accepted is True
    assert result.task_id == "relay-pending-agent"


@pytest.mark.asyncio
async def test_facade_offline_dispatch_replays_when_in_memory_stream_connects() -> None:
    facade = HubFacade()
    result = await facade.send_to_hub(
        HubDispatchCommand(
            hub_id="hub-1",
            agent_id="agent-1",
            local_agent_id="local-1",
            room_id="room-1",
            user_message_id="user-1",
            agent_message_id="agent-msg-1",
            payload={"body": "hello"},
            task_id="relay-pending-agent",
        )
    )

    stream = facade.connect_hub_stream("hub-1")

    assert result.accepted is True
    assert await anext(stream) == {"type": "connection_ready"}
    event = await asyncio.wait_for(anext(stream), timeout=1)
    assert event["type"] == "user_message"
    assert event["agent_message_id"] == "agent-msg-1"
    assert "task_id" not in event
    await stream.aclose()


@pytest.mark.asyncio
async def test_facade_preserves_unconsumed_live_queue_events_on_disconnect() -> None:
    facade = HubFacade()
    stream = facade.connect_hub_stream("hub-1")
    assert await anext(stream) == {"type": "connection_ready"}

    result = await facade.send_to_hub(
        HubDispatchCommand(
            hub_id="hub-1",
            agent_id="agent-1",
            local_agent_id="local-1",
            room_id="room-1",
            user_message_id="user-1",
            agent_message_id="agent-msg-1",
            payload={"body": "hello"},
            task_id="relay-pending-agent",
        )
    )
    await stream.aclose()

    replay = facade.connect_hub_stream("hub-1")
    assert result.accepted is True
    assert await anext(replay) == {"type": "connection_ready"}
    event = await asyncio.wait_for(anext(replay), timeout=1)
    assert event["type"] == "user_message"
    assert event["agent_message_id"] == "agent-msg-1"
    await replay.aclose()


@pytest.mark.asyncio
async def test_facade_marks_dropped_offline_queue_event_failed() -> None:
    failures = OfflineFailures()
    facade = HubFacade(
        HubRuntimeBridgeDeps(
            config=HubRuntimeBridgeConfig(offline_queue_max=1),
            offline_failure_port=failures,
        )
    )

    for index in range(2):
        await facade.send_to_hub(
            HubDispatchCommand(
                hub_id="hub-1",
                agent_id="agent-1",
                local_agent_id="local-1",
                room_id="room-1",
                user_message_id=f"user-{index}",
                agent_message_id=f"agent-msg-{index}",
                payload={"body": "hello"},
                task_id=f"task-{index}",
            )
        )

    assert len(failures.commands) == 1
    assert failures.commands[0].agent_message_id == "agent-msg-0"
    assert failures.commands[0].task_id == "agent-msg-0"


@pytest.mark.asyncio
async def test_facade_stream_stays_open_and_delivers_local_events() -> None:
    facade = HubFacade()
    stream = facade.connect_hub_stream("hub-1")

    assert await anext(stream) == {"type": "connection_ready"}
    result = await facade.send_to_hub(
        HubDispatchCommand(
            hub_id="hub-1",
            agent_id="agent-1",
            local_agent_id="local-1",
            room_id="room-1",
            user_message_id="user-1",
            agent_message_id="agent-msg-1",
            payload={"body": "hello"},
            task_id="relay-pending-agent",
        )
    )
    event = await asyncio.wait_for(anext(stream), timeout=1)

    assert result.accepted is True
    assert event["type"] == "user_message"
    assert event["agent_message_id"] == "agent-msg-1"
    await stream.aclose()


@pytest.mark.asyncio
async def test_facade_stream_immediate_close_clears_liveness() -> None:
    facade = HubFacade()
    stream = facade.connect_hub_stream("hub-1")

    assert await anext(stream) == {"type": "connection_ready"}
    await stream.aclose()

    assert facade.is_hub_online_cached("hub-1") is False


@pytest.mark.asyncio
async def test_facade_replacement_stream_keeps_liveness_online() -> None:
    facade = HubFacade()
    first = facade.connect_hub_stream("hub-1")
    second = facade.connect_hub_stream("hub-1")

    assert await anext(first) == {"type": "connection_ready"}
    assert await anext(second) == {"type": "connection_ready"}
    with pytest.raises(StopAsyncIteration):
        await anext(first)

    await first.aclose()
    assert facade.is_hub_online_cached("hub-1") is True
    await second.aclose()


@pytest.mark.asyncio
async def test_facade_stop_disconnects_active_streams() -> None:
    facade = HubFacade()
    stream = facade.connect_hub_stream("hub-1")

    assert await anext(stream) == {"type": "connection_ready"}
    pending = asyncio.ensure_future(anext(stream))
    await facade.stop()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending, timeout=1)


@pytest.mark.asyncio
async def test_facade_in_memory_stream_emits_idle_heartbeat() -> None:
    facade = HubFacade(
        HubRuntimeBridgeDeps(
            config=HubRuntimeBridgeConfig(heartbeat_interval_seconds=1)
        )
    )
    stream = facade.connect_hub_stream("hub-1")

    assert await anext(stream) == {"type": "connection_ready"}
    heartbeat = await asyncio.wait_for(anext(stream), timeout=2)

    assert heartbeat["type"] == "heartbeat"
    await stream.aclose()


@pytest.mark.asyncio
async def test_facade_streams_replacement_disconnects_stale_iterator() -> None:
    class Streams:
        async def record_heartbeat(self, hub_id):
            return None

        async def read_events(self, hub_id, last_id="$", block_ms=5000):
            await asyncio.sleep(10)
            return []

    facade = HubFacade(
        HubRuntimeBridgeDeps(config=HubRuntimeBridgeConfig(), streams=Streams())
    )
    first = facade.connect_hub_stream("hub-1")
    second = facade.connect_hub_stream("hub-1")

    assert await anext(first) == {"type": "connection_ready"}
    assert await anext(second) == {"type": "connection_ready"}
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(first), timeout=1)
    assert facade.is_hub_online_cached("hub-1") is True
    await second.aclose()


@pytest.mark.asyncio
async def test_facade_start_replays_unprocessed_hub_response_journal() -> None:
    journal = InMemoryHubResponseJournal()
    await journal.create_or_get(
        {
            "hub_id": "hub-1",
            "room_id": "room-1",
            "agent_message_id": "msg-1",
            "task_id": "task-1",
            "event_type": "agent_response",
            "payload": {
                "kind": "response",
                "message_id": "msg-1",
                "task_id": "task-1",
                "text": "replayed",
            },
            "idempotency_key": "ingest:replay",
        }
    )
    sink = Sink()
    facade = HubFacade(
        HubRuntimeBridgeDeps(
            config=HubRuntimeBridgeConfig(replay_interval_seconds=60),
            hub_response_journal=journal,
            worker_id="worker-1",
        )
    )
    facade.bind_internal_response_dispatcher(
        HubInternalResponseRouter(
            sink=sink,
            journal=journal,
            worker_id="worker-1",
        )
    )

    await facade.start()
    await facade.stop()

    assert len(sink.events) == 1
    assert sink.events[0].payload["text"] == "replayed"


@pytest.mark.asyncio
async def test_facade_stream_append_failure_does_not_mark_hub_offline_failure() -> None:
    class Streams:
        async def is_hub_alive(self, hub_id):
            return True

        async def push_event(self, hub_id, event):
            return False

    failures = OfflineFailures()
    facade = HubFacade(
        HubRuntimeBridgeDeps(
            config=HubRuntimeBridgeConfig(),
            streams=Streams(),
            offline_failure_port=failures,
        )
    )

    result = await facade.send_to_hub(
        HubDispatchCommand(
            hub_id="hub-1",
            agent_id="agent-1",
            local_agent_id="local-1",
            room_id="room-1",
            user_message_id="user-1",
            agent_message_id="agent-msg-1",
            payload={"body": "hello"},
            task_id="task-1",
        )
    )

    assert result.accepted is False
    assert result.error == "hub_dispatch_failed"
    assert failures.commands == []


@pytest.mark.asyncio
async def test_facade_stream_entry_id_counts_as_successful_dispatch() -> None:
    class Streams:
        async def is_hub_alive(self, hub_id):
            return True

        async def push_event(self, hub_id, event):
            return "1-0"

    facade = HubFacade(
        HubRuntimeBridgeDeps(
            config=HubRuntimeBridgeConfig(),
            streams=Streams(),
        )
    )

    result = await facade.send_to_hub(
        HubDispatchCommand(
            hub_id="hub-1",
            agent_id="agent-1",
            local_agent_id="local-1",
            room_id="room-1",
            user_message_id="user-1",
            agent_message_id="agent-msg-1",
            payload={"body": "hello"},
            task_id="task-1",
        )
    )

    assert result.accepted is True
    assert result.error is None


@pytest.mark.asyncio
async def test_hub_facade_bind_streams_updates_liveness_service() -> None:
    facade = HubFacade()
    streams = _Streams()

    facade.bind_streams(streams)
    await streams.record_heartbeat("hub-1")

    assert await facade.is_hub_online("hub-1") is True


@pytest.mark.asyncio
async def test_hub_facade_bind_agent_registry_writer_updates_sync_service() -> None:
    facade = HubFacade()
    writer = _Writer()

    facade.bind_agent_registry_writer(writer)

    assert await facade.sync_agents("hub-1", [], "owner-1") == []
    assert writer.calls == [("hub-1", "owner-1", [], True)]


@pytest.mark.asyncio
async def test_hub_facade_bind_streams_refreshes_connection_liveness() -> None:
    facade = HubFacade(
        deps=HubRuntimeBridgeDeps(
            config=HubRuntimeBridgeConfig(),
            hub_repository=_RepositoryForConnectionLiveness(),
        )
    )
    streams = _Streams()

    facade.bind_streams(streams)
    await streams.record_heartbeat("hub-1")

    hubs = await facade.list_hubs("owner-1")
    assert hubs[0].is_online is True


def test_hub_facade_exposes_worker_and_ownership_dependencies() -> None:
    store = _OwnershipStore()
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        task_ownership_store=store,
        worker_id="worker-1",
    )
    facade = HubFacade(deps=deps)

    assert facade.worker_id == "worker-1"
    assert facade.task_ownership_store is store
    assert facade.ownership_maintainer is facade.ownership_lease_maintainer


def test_hub_facade_bind_internal_response_sink_owns_router_creation() -> None:
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_response_journal=InMemoryHubResponseJournal(),
        task_ownership_store=_OwnershipStore(),
        worker_id="worker-1",
    )
    facade = HubFacade(deps=deps)

    sink = Sink()
    router = facade.bind_internal_response_sink(sink)

    assert router is not None
    assert facade.internal_response_dispatcher is router


@pytest.mark.asyncio
async def test_hub_facade_bind_internal_response_sink_receives_publish_event() -> None:
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_response_journal=InMemoryHubResponseJournal(),
        task_ownership_store=_OwnershipStore(),
        worker_id="worker-1",
    )
    facade = HubFacade(deps=deps)
    sink = Sink()
    facade.bind_internal_response_sink(sink)

    await facade.publish_from_hub(
        "hub-1",
        {
            "owner_id": "owner-1",
            "room_id": "room-1",
            "events": [
                {
                    "type": "agent_response",
                    "agent_message_id": "agent-message-1",
                    "data": {
                        "task_id": "task-1",
                        "content": "done",
                        "response_seq": 1,
                    },
                }
            ],
        },
    )

    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_hub_facade_bind_internal_response_sink_replaces_started_replay_worker_dispatcher() -> (
    None
):
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_response_journal=InMemoryHubResponseJournal(),
        task_ownership_store=_OwnershipStore(),
        worker_id="worker-1",
    )
    facade = HubFacade(deps=deps)
    first_router = facade.bind_internal_response_sink(Sink())
    await facade.start()
    first_worker = facade._replay_worker
    assert first_worker is not None
    assert first_worker._dispatcher is first_router

    second_router = facade.bind_internal_response_sink(Sink())

    assert facade.internal_response_dispatcher is second_router
    assert facade._replay_worker is not None
    assert facade._replay_worker is not first_worker
    assert facade._replay_worker._dispatcher is second_router
    await facade.stop()


@pytest.mark.asyncio
async def test_hub_facade_stop_cancels_pending_replay_worker_restart() -> None:
    scheduled = []

    def capture_task(coro, **kwargs):
        task = asyncio.create_task(coro)
        scheduled.append(task)
        return task

    class ReplayWorkerSpy:
        def __init__(self, name: str) -> None:
            self.name = name
            self.starts = 0
            self.stops = 0

        async def start(self) -> None:
            self.starts += 1

        async def stop(self) -> None:
            self.stops += 1

    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_response_journal=InMemoryHubResponseJournal(),
        worker_id="worker-1",
        task_runner=capture_task,
    )
    facade = HubFacade(deps=deps)
    first_worker = ReplayWorkerSpy("first")
    second_worker = ReplayWorkerSpy("second")
    third_worker = ReplayWorkerSpy("third")
    workers = iter([first_worker, second_worker, third_worker])
    facade._build_replay_worker = lambda: next(workers)

    facade.bind_internal_response_sink(Sink())
    await facade.start()

    facade.bind_internal_response_sink(Sink())
    facade.bind_internal_response_sink(Sink())
    assert scheduled

    await facade.stop()

    assert facade._started is False
    assert facade._replay_worker_restart_task is None
    assert first_worker.starts == 1
    assert first_worker.stops == 1
    assert second_worker.starts == 0
    assert second_worker.stops == 1
    assert third_worker.starts == 0
    assert third_worker.stops == 1
    assert all(task.done() or task.cancelled() for task in scheduled)


class _HubRepositoryForSweep:
    def __init__(self) -> None:
        self.guarded_offline: list[tuple[str, str | None]] = []
        self.updated_offline: list[str] = []
        self.updated_online: list[str] = []

    async def list_online_hubs_for_liveness(self) -> list[dict]:
        return [{"hub_id": "hub-offline", "connection_id": "conn-1"}]

    async def list_offline_hubs_for_recovery(self, limit: int) -> list[dict]:
        assert limit == 100
        return [{"hub_id": "hub-recovered"}]

    async def update_hub_status_if_current(
        self,
        hub_id: str,
        *,
        connection_id: str | None,
        is_online: bool,
    ) -> bool:
        if is_online is False:
            self.guarded_offline.append((hub_id, connection_id))
        return True

    async def update_hub_status(self, hub_id: str, **kwargs) -> None:
        if kwargs.get("is_online") is False:
            self.updated_offline.append(hub_id)
        if kwargs.get("is_online") is True:
            self.updated_online.append(hub_id)


class _StreamsForSweep:
    async def is_hub_alive(self, hub_id: str) -> bool:
        return hub_id == "hub-recovered"


class _WriterForSweep:
    def __init__(self) -> None:
        self.offline_hubs: list[str] = []

    async def mark_hub_agents_offline(self, hub_id: str) -> None:
        self.offline_hubs.append(hub_id)

    async def sync_hub_agents(self, *args, **kwargs) -> list:
        return []


@pytest.mark.asyncio
async def test_hub_facade_sweep_stream_liveness_uses_repository_streams_and_writer() -> (
    None
):
    repository = _HubRepositoryForSweep()
    writer = _WriterForSweep()
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_repository=repository,
        streams=_StreamsForSweep(),
        agent_registry_writer=writer,
    )
    facade = HubFacade(deps=deps)

    stale_hubs = await facade.sweep_stream_liveness()

    assert [event.hub_id for event in stale_hubs] == ["hub-offline"]
    assert [event.connection_id for event in stale_hubs] == ["conn-1"]
    assert repository.guarded_offline == [("hub-offline", "conn-1")]
    assert writer.offline_hubs == ["hub-offline"]
    assert repository.updated_online == ["hub-recovered"]


class _HubRepositoryForSweepWithoutConnectionId(_HubRepositoryForSweep):
    async def list_online_hubs_for_liveness(self) -> list[dict]:
        return [{"hub_id": "hub-offline", "connection_id": None}]

    async def update_hub_status_if_current(self, *args, **kwargs) -> bool:
        raise AssertionError("guarded update should not run without connection_id")


@pytest.mark.asyncio
async def test_hub_facade_sweep_stream_liveness_without_connection_id_uses_unconditional_update() -> (
    None
):
    repository = _HubRepositoryForSweepWithoutConnectionId()
    writer = _WriterForSweep()
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_repository=repository,
        streams=_StreamsForSweep(),
        agent_registry_writer=writer,
    )
    facade = HubFacade(deps=deps)

    stale_hubs = await facade.sweep_stream_liveness()

    assert [event.hub_id for event in stale_hubs] == ["hub-offline"]
    assert [event.connection_id for event in stale_hubs] == [None]
    assert repository.updated_offline == ["hub-offline"]
    assert writer.offline_hubs == ["hub-offline"]


class _HubRepositoryForSweepGuardMismatch(_HubRepositoryForSweep):
    async def update_hub_status_if_current(
        self,
        hub_id: str,
        *,
        connection_id: str | None,
        is_online: bool,
    ) -> bool:
        self.guarded_offline.append((hub_id, connection_id))
        return False


@pytest.mark.asyncio
async def test_hub_facade_sweep_stream_liveness_returns_stale_hub_when_guard_fails() -> (
    None
):
    repository = _HubRepositoryForSweepGuardMismatch()
    writer = _WriterForSweep()
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_repository=repository,
        streams=_StreamsForSweep(),
        agent_registry_writer=writer,
    )
    facade = HubFacade(deps=deps)

    stale_hubs = await facade.sweep_stream_liveness()

    assert [event.hub_id for event in stale_hubs] == ["hub-offline"]
    assert [event.connection_id for event in stale_hubs] == ["conn-1"]
    assert repository.guarded_offline == [("hub-offline", "conn-1")]
    assert writer.offline_hubs == []
