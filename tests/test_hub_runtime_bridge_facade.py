from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from common.dto import HubAgentCounts, HubDispatchCommand
from hub_runtime_bridge.hub_response_journal import InMemoryHubResponseJournal
from hub_runtime_bridge.hub_response_journal import MongoHubResponseJournal
from hub_runtime_bridge.internal_response_router import HubInternalResponseRouter
from hub_runtime_bridge.task_ownership import MongoHubTaskOwnershipStore
from hub_runtime_bridge import HubFacade, HubRuntimeBridgeConfig, HubRuntimeBridgeDeps


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
        self.hubs.setdefault(hub_id, {"hub_id": hub_id, "user_id": "owner"}).update(fields)


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

    facade = HubFacade(HubRuntimeBridgeDeps(config=HubRuntimeBridgeConfig(), streams=Streams()))
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
