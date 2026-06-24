"""
Unit tests for Relay API endpoints and RelayService.

Tests cover:
- Hub registration (success, duplicate, invalid key)
- Agent sync (upsert, dedup by hub_id+local_agent_id, agent_card.url rewriting)
- SSE events endpoint (connection, message delivery)
- Publish endpoint (API key auth, room ownership auth, event persistence, resume)
- Hub status endpoint
- Offline queue behavior (enqueue, overflow)
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.dto import HubDispatchCommand
from common.dto.agent import SyncedHubAgent
from execution.dispatch.agent_event import AgentEvent
from execution.dispatch.response_handler import AgentResponseHandler
from execution.dispatch.transports.relay import RelayTransport
from hub_runtime_bridge.adapters.legacy_publish import (
    LegacyHubPublishAuthorizationReader,
)
from hub_runtime_bridge.compat.relay_service import (
    RelayService,
    _LegacyPublishSink,
    init_relay_service,
)
from hub_runtime_bridge.config import HubRuntimeBridgeConfig
from models.api_key import APIKey
from models.hub import (
    HubAgentSync,
    HubPublishEvent,
    HubPublishRequest,
    RelayToHubEvent,
)
from models.room import MessageContent, Room, RoomAgentMessage
from tests.conftest import FROZEN_TIME

# ===========================================================================
# Helpers
# ===========================================================================


def _make_api_key(user_id: str = "user-001") -> APIKey:
    return APIKey(
        key_id="key-001",
        key_hash="abc123hash",
        user_id=user_id,
        name="Test Key",
        created_at=FROZEN_TIME,
    )


def _make_agent_card(name: str = "TestHub", url: str = "http://localhost:8000"):
    return dict(
        name=name,
        url=url,
        version="1.0",
        skills=[],
        description="A hub test agent",
        capabilities={"streaming": False},
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
    )


def _make_relay_service(
    *,
    mongo=None,
    db_service=None,
    sse_manager=None,
    offline_failure_port=None,
    config=None,
) -> RelayService:
    if mongo is None:
        mongo = MagicMock()
        mongo.upsert_hub = AsyncMock()
        mongo.get_hub = AsyncMock(return_value=None)
        mongo.get_hubs_by_user = AsyncMock(return_value=[])
        mongo.update_hub_status = AsyncMock()
        mongo.update_hub_status_if_current = AsyncMock(return_value=True)
        mongo.upsert_hub_agent = AsyncMock(return_value="agent-new-001")
        mongo.count_hub_agents = AsyncMock(return_value=(0, 0))
        mongo.agents_collection = MagicMock()
        mongo.agents_collection.find_one = AsyncMock(return_value=None)
        mongo.agents_collection.update_one = AsyncMock()
        mongo.agents_collection.update_many = AsyncMock()
    if db_service is None:
        db_service = MagicMock()
        db_service.get_room_by_room_id = AsyncMock(return_value=None)
        db_service.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
        db_service.update_room_agent_message_by_message_id = AsyncMock(
            return_value=True
        )
        db_service.update_task_state_on_message = AsyncMock(return_value=(True, None))
        db_service.is_message_cancelled = AsyncMock(return_value=False)
        db_service.ai_service.get_embedding = AsyncMock(return_value=[0.0] * 128)
        db_service.pinecone.upsert = MagicMock()
    if sse_manager is None:
        sse_manager = MagicMock()
        sse_manager.send_agent_response = AsyncMock()
        sse_manager.send_task_submitted = AsyncMock()
        sse_manager.send_processing_status = AsyncMock()
        sse_manager.send_error = AsyncMock()
    if offline_failure_port is None:
        offline_failure_port = MagicMock()
        offline_failure_port.mark_hub_message_failed = AsyncMock()

    svc = RelayService(
        mongo=mongo,
        db=db_service,
        sse_manager=sse_manager,
        offline_failure_port=offline_failure_port,
        config=config,
    )

    # Wire up RelayTransport for publish event delegation
    handler = MagicMock(spec=AgentResponseHandler)
    handler.handle = AsyncMock()
    relay_transport = RelayTransport(
        response_handler=handler,
        relay_service=svc,
        task_tracker=db_service,
        call_counter=db_service,
        delivery=sse_manager,
    )
    svc.set_relay_transport(relay_transport)

    return svc


@pytest.mark.asyncio
async def test_hub_facade_uses_injected_offline_failure_port_for_legacy_rejections():
    offline_failure_port = MagicMock()
    offline_failure_port.mark_hub_message_failed = AsyncMock()
    svc = _make_relay_service(offline_failure_port=offline_failure_port)

    await svc._facade._mark_rejected_legacy_event(
        {
            "type": "user_message",
            "room_id": "room-1",
            "agent_message_id": "agent-message-1",
            "agent_id": "agent-1",
            "task_id": "task-1",
        },
        "Agent is offline",
    )

    offline_failure_port.mark_hub_message_failed.assert_awaited_once()
    command = offline_failure_port.mark_hub_message_failed.await_args.args[0]
    assert command.room_id == "room-1"
    assert command.agent_message_id == "agent-message-1"
    assert command.error_text == "Agent is offline"


def test_init_relay_service_binds_hitl_coordinator_to_publish_handler():
    mongo = MagicMock()
    db_service = MagicMock()
    sse_manager = MagicMock()
    room_message_center = MagicMock()
    hitl_coordinator = MagicMock()

    svc = init_relay_service(
        mongo=mongo,
        db=db_service,
        sse_manager=sse_manager,
        room_message_center=room_message_center,
        hitl_coordinator=hitl_coordinator,
        offline_failure_port=MagicMock(),
    )

    assert svc._response_handler.hitl_coordinator is hitl_coordinator


def test_init_relay_service_requires_injected_response_handler():
    mongo = MagicMock()
    db_service = MagicMock()
    sse_manager = MagicMock()
    room_message_center = SimpleNamespace()
    hitl_coordinator = MagicMock()

    with pytest.raises(ValueError, match="agent_response_handler"):
        init_relay_service(
            mongo=mongo,
            db=db_service,
            sse_manager=sse_manager,
            room_message_center=room_message_center,
            hitl_coordinator=hitl_coordinator,
            offline_failure_port=MagicMock(),
        )


def test_init_relay_service_requires_offline_failure_port():
    with pytest.raises(ValueError, match="offline_failure_port"):
        init_relay_service(
            mongo=MagicMock(),
            db=MagicMock(),
            sse_manager=MagicMock(),
            room_message_center=MagicMock(agent_response_handler=MagicMock()),
        )


def test_init_relay_service_binds_processor_relay_path():
    mongo = MagicMock()
    db_service = MagicMock()
    sse_manager = MagicMock()
    hitl_coordinator = MagicMock()

    processor = MagicMock()
    processor.bind_relay_service = MagicMock()
    response_handler = MagicMock()
    room_message_center = MagicMock(
        agent_message_processor=processor,
        agent_response_handler=response_handler,
    )

    svc = init_relay_service(
        mongo=mongo,
        db=db_service,
        sse_manager=sse_manager,
        room_message_center=room_message_center,
        hitl_coordinator=hitl_coordinator,
        offline_failure_port=MagicMock(),
    )

    assert svc.relay_transport is None
    assert svc._publish_handler is None
    assert svc._facade.deps.publish_authorization_reader is not None
    assert svc._facade.deps.cancellation_reader is not None
    assert svc.internal_response_dispatcher is not None
    assert svc._facade._dispatcher is svc.internal_response_dispatcher
    assert svc._response_handler is response_handler
    assert response_handler.hitl_coordinator is hitl_coordinator
    processor.bind_relay_service.assert_called_once_with(svc)


def test_init_relay_service_wires_hub_worker_and_event_publisher():
    publisher = MagicMock()
    converter = MagicMock()
    svc = init_relay_service(
        mongo=MagicMock(),
        db=MagicMock(),
        sse_manager=MagicMock(),
        room_message_center=MagicMock(agent_response_handler=MagicMock()),
        event_publisher=publisher,
        worker_id="worker-123",
        response_converter=converter,
        offline_failure_port=MagicMock(),
    )

    assert svc.worker_id == "worker-123"
    assert svc._facade.deps.event_publisher is publisher
    assert svc._response_converter is converter


@pytest.mark.asyncio
async def test_legacy_publish_sink_delivers_internal_response_to_response_handler():
    response_handler = MagicMock()
    response_handler.handle = AsyncMock()
    relay = SimpleNamespace(_response_handler=response_handler)
    sink = _LegacyPublishSink(
        relay,
        response_converter=lambda event: AgentEvent(
            kind="response",
            room_id=event.room_id,
            message_id=event.payload["message_id"],
            agent_id=event.agent_id,
            task_id=event.task_id,
            text=event.payload["text"],
        ),
    )

    await sink.handle_hub_agent_response(
        MagicMock(
            hub_id="hub-1",
            agent_id="agent-1",
            room_id="room-1",
            task_id="task-1",
            is_terminal=True,
            payload={
                "kind": "response",
                "message_id": "msg-1",
                "text": "ok",
            },
        )
    )

    response_handler.handle.assert_awaited_once()
    event = response_handler.handle.await_args.args[0]
    assert event.kind == "response"
    assert event.text == "ok"


@pytest.mark.asyncio
async def test_legacy_publish_sink_ignores_missing_response_handler():
    relay = SimpleNamespace(_response_handler=None)
    sink = _LegacyPublishSink(relay)

    await sink.handle_hub_agent_response(
        MagicMock(
            hub_id="hub-1",
            agent_id="agent-1",
            room_id="room-1",
            task_id="task-1",
            is_terminal=True,
            payload={
                "kind": "response",
                "message_id": "msg-1",
                "text": "ok",
            },
        )
    )


@pytest.mark.asyncio
async def test_relay_publish_authorization_uses_related_message_as_legacy_lifecycle_id():
    db = MagicMock()
    msg = RoomAgentMessage(
        room_id="room-1",
        message_id="amsg-001",
        agent_id="agent-001",
        related_message_id="umsg-001",
        message_content=MessageContent(message_text=""),
    )
    db.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
    db.get_agent_by_agent_id = AsyncMock(return_value=MagicMock(hub_id="hub-001"))
    reader = LegacyHubPublishAuthorizationReader(db)

    lineage = await reader.authorize_hub_publish(
        hub_id="hub-001",
        owner_id="user-001",
        room_id="room-1",
        agent_message_id="amsg-001",
    )

    assert lineage is not None
    assert lineage.root_user_message_id == "umsg-001"
    assert lineage.lifecycle_message_id == "umsg-001"


@pytest.mark.asyncio
async def test_relay_publish_authorization_walks_agent_parent_chain_to_root_user():
    db = MagicMock()
    msg = RoomAgentMessage(
        room_id="room-1",
        message_id="amsg-child",
        agent_id="agent-001",
        related_message_id="amsg-parent",
        message_content=MessageContent(message_text=""),
    )
    parent = RoomAgentMessage(
        room_id="room-1",
        message_id="amsg-parent",
        agent_id="agent-001",
        related_message_id="umsg-root",
        message_content=MessageContent(message_text=""),
    )
    db.get_room_agent_message_by_message_id = AsyncMock(side_effect=[msg, parent])
    db.get_room_user_message_by_message_id = AsyncMock(
        side_effect=[None, MagicMock(message_type="user")]
    )
    db.get_agent_by_agent_id = AsyncMock(return_value=MagicMock(hub_id="hub-001"))
    reader = LegacyHubPublishAuthorizationReader(db)

    lineage = await reader.authorize_hub_publish(
        hub_id="hub-001",
        owner_id="user-001",
        room_id="room-1",
        agent_message_id="amsg-child",
    )

    assert lineage is not None
    assert lineage.root_user_message_id == "umsg-root"
    assert lineage.lifecycle_message_id == "umsg-root"


def _make_writer():
    writer = MagicMock()

    async def sync_hub_agents(hub_id, owner_user_id, descriptors, prune_missing=True):
        return [
            SyncedHubAgent(
                hub_id=hub_id,
                agent_id=f"stored-{descriptor.agent_id}",
                descriptor=descriptor,
            )
            for descriptor in descriptors
        ]

    writer.sync_hub_agents = AsyncMock(side_effect=sync_hub_agents)
    writer.mark_hub_agents_offline = AsyncMock()
    return writer


# ===========================================================================
# RelayService — Registration
# ===========================================================================


class TestRelayServiceRegistration:
    @pytest.mark.asyncio
    async def test_register_hub_success(self):
        svc = _make_relay_service()
        key = _make_api_key()
        hub = await svc.register_hub("hub-001", key)

        assert hub.hub_id == "hub-001"
        assert hub.user_id == "user-001"
        svc._mongo.upsert_hub.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_register_hub_duplicate_upserts(self):
        svc = _make_relay_service()
        key = _make_api_key()
        await svc.register_hub("hub-001", key)
        await svc.register_hub("hub-001", key)
        assert svc._mongo.upsert_hub.await_count == 2

    @pytest.mark.asyncio
    async def test_stop_stops_hub_facade(self):
        svc = _make_relay_service()
        svc._facade.stop = AsyncMock()

        await svc.stop()

        svc._facade.stop.assert_awaited_once()


# ===========================================================================
# RelayService — Hub Connection Checks
# ===========================================================================


class TestIsHubConnectedLocally:
    def test_returns_false_when_not_connected(self):
        svc = _make_relay_service()
        assert svc._is_hub_connected_locally("hub-001") is False

    @pytest.mark.asyncio
    async def test_returns_true_for_live_facade_stream(self):
        svc = _make_relay_service()
        stream = svc._facade.connect_hub_stream("hub-001")

        assert await stream.__anext__() == {"type": "connection_ready"}
        assert svc._is_hub_connected_locally("hub-001") is True

        await stream.aclose()


# ===========================================================================
# RelayService — Agent Sync
# ===========================================================================


class TestRelayServiceAgentSync:
    @pytest.mark.asyncio
    async def test_sync_agents_requires_registry_writer(self):
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}

        with pytest.raises(RuntimeError, match="AgentRegistryWriter"):
            await svc.sync_agents("hub-001", [], _make_api_key())

        svc._mongo.upsert_hub_agent.assert_not_awaited()
        svc._mongo.agents_collection.update_many.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_agents_delegates_valid_agents_to_writer(self):
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)

        agents = [
            HubAgentSync(
                local_agent_id="local-1",
                name="Agent A",
                description="Desc A",
                capabilities=["calendar"],
                agent_card=_make_agent_card("Agent A", url="https://agent.example.com"),
            ),
        ]
        synced = await svc.sync_agents("hub-001", agents, _make_api_key())

        assert synced == [{"agent_id": "stored-local-1", "local_agent_id": "local-1"}]
        writer.sync_hub_agents.assert_awaited_once()
        hub_id, owner_user_id, descriptors = writer.sync_hub_agents.await_args.args
        assert hub_id == "hub-001"
        assert owner_user_id == "user-001"
        assert writer.sync_hub_agents.await_args.kwargs == {"prune_missing": True}
        assert len(descriptors) == 1
        descriptor = descriptors[0]
        assert descriptor.agent_id == "local-1"
        assert descriptor.name == "Agent A"
        assert descriptor.url == "https://agent.example.com"
        assert descriptor.capabilities == ["calendar"]
        assert descriptor.raw_card["name"] == "Agent A"
        svc._mongo.upsert_hub_agent.assert_not_awaited()
        svc._mongo.agents_collection.update_many.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_agents_rejects_wrong_owner(self):
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "other-user"}

        key = _make_api_key(user_id="user-001")
        with pytest.raises(PermissionError):
            await svc.sync_agents("hub-001", [], key)

    @pytest.mark.asyncio
    async def test_sync_empty_with_prune_missing_delegates_to_writer(self):
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)

        synced = await svc.sync_agents(
            "hub-001",
            [],
            _make_api_key(),
            prune_missing=True,
        )

        assert synced == []
        writer.sync_hub_agents.assert_awaited_once()
        assert writer.sync_hub_agents.await_args.args[2] == []
        assert writer.sync_hub_agents.await_args.kwargs == {"prune_missing": True}
        svc._mongo.agents_collection.update_many.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_empty_without_prune_missing_delegates_to_writer(self):
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)

        synced = await svc.sync_agents(
            "hub-001",
            [],
            _make_api_key(),
            prune_missing=False,
        )

        assert synced == []
        writer.sync_hub_agents.assert_awaited_once()
        assert writer.sync_hub_agents.await_args.args[2] == []
        assert writer.sync_hub_agents.await_args.kwargs == {"prune_missing": False}
        svc._mongo.agents_collection.update_many.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_agents_skips_invalid_agent_card(self):
        """Agents with an invalid agent_card (e.g. error dict) must be skipped."""
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)

        agents = [
            HubAgentSync(
                local_agent_id="corrupt-1",
                name="Corrupt Agent",
                description="Desc",
                agent_card={"error": "Unexpected endpoint or method."},
            ),
            HubAgentSync(
                local_agent_id="local-1",
                name="Agent A",
                description="Desc",
                agent_card=_make_agent_card("Agent A"),
            ),
        ]
        synced = await svc.sync_agents("hub-001", agents, _make_api_key())

        assert len(synced) == 1
        assert synced[0]["local_agent_id"] == "local-1"
        writer.sync_hub_agents.assert_awaited_once()
        descriptors = writer.sync_hub_agents.await_args.args[2]
        assert [descriptor.agent_id for descriptor in descriptors] == ["local-1"]

    @pytest.mark.asyncio
    async def test_sync_agents_refreshes_redis_heartbeat_when_streams_enabled(self):
        streams = MagicMock()
        streams.record_heartbeat = AsyncMock()
        streams.is_hub_alive = AsyncMock(return_value=True)

        svc = _make_relay_service()
        svc.set_stream_service(streams)
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)

        agents = [
            HubAgentSync(
                local_agent_id="local-1",
                name="Agent A",
                description="Desc",
                agent_card=_make_agent_card("Agent A"),
            ),
        ]
        await svc.sync_agents("hub-001", agents, _make_api_key())

        streams.record_heartbeat.assert_awaited()
        assert streams.record_heartbeat.await_args_list[0].args[0] == "hub-001"

    @pytest.mark.asyncio
    async def test_sync_all_invalid_agent_cards_skips_prune(self):
        """Do not prune every hub agent when every card in the batch is invalid."""
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)

        agents = [
            HubAgentSync(
                local_agent_id="bad-1",
                name="Bad",
                description="Desc",
                agent_card={"error": "Unexpected endpoint or method."},
            ),
        ]
        synced = await svc.sync_agents("hub-001", agents, _make_api_key())

        assert synced == []
        writer.sync_hub_agents.assert_not_awaited()
        svc._mongo.agents_collection.update_many.assert_not_awaited()


# ===========================================================================
# RelayService — Push to Hub
# ===========================================================================


class TestRelayServicePush:
    @pytest.mark.asyncio
    async def test_push_to_online_hub(self):
        svc = _make_relay_service()
        stream = svc._facade.connect_hub_stream("hub-001")
        assert await stream.__anext__() == {"type": "connection_ready"}

        event = RelayToHubEvent(type="user_message", room_id="room-1")
        delivered = await svc.push_to_hub("hub-001", event)

        assert delivered is True
        queued = await stream.__anext__()
        assert queued["type"] == "user_message"
        await stream.aclose()

    @pytest.mark.asyncio
    async def test_push_to_offline_hub_queues(self):
        offline_failure_port = MagicMock()
        offline_failure_port.mark_hub_message_failed = AsyncMock()
        svc = _make_relay_service(offline_failure_port=offline_failure_port)
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)

        event = RelayToHubEvent(type="user_message", room_id="room-1")
        delivered = await svc.push_to_hub("hub-002", event)

        assert delivered is False
        assert len(svc._facade._offline_queues["hub-002"]) == 1
        writer.mark_hub_agents_offline.assert_awaited_once_with("hub-002")
        offline_failure_port.mark_hub_message_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_push_to_offline_streams_hub_marks_failed_without_queue(self):
        offline_failure_port = MagicMock()
        offline_failure_port.mark_hub_message_failed = AsyncMock()
        svc = _make_relay_service(offline_failure_port=offline_failure_port)
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)
        streams = MagicMock()
        streams.is_hub_alive = AsyncMock(return_value=False)
        svc._facade.bind_streams(streams)
        svc._facade._hub_disconnected_at["hub-002"] = time.monotonic()

        event = RelayToHubEvent(type="user_message", room_id="room-1")
        delivered = await svc.push_to_hub("hub-002", event)

        assert delivered is False
        writer.mark_hub_agents_offline.assert_awaited_once_with("hub-002")
        assert "hub-002" not in svc._facade._offline_queues
        offline_failure_port.mark_hub_message_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_push_to_disconnected_in_memory_hub_marks_failed_after_grace_period(
        self,
    ):
        offline_failure_port = MagicMock()
        offline_failure_port.mark_hub_message_failed = AsyncMock()
        svc = _make_relay_service(
            offline_failure_port=offline_failure_port,
            config=HubRuntimeBridgeConfig(offline_grace_period_seconds=1),
        )
        svc._facade._hub_disconnected_at["hub-002"] = time.monotonic() - 2

        event = RelayToHubEvent(
            type="user_message",
            room_id="room-1",
            agent_message_id="msg-1",
        )
        delivered = await svc.push_to_hub("hub-002", event)

        assert delivered is False
        assert "hub-002" not in svc._facade._offline_queues
        offline_failure_port.mark_hub_message_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_offline_queue_overflow_drops_oldest(self):
        svc = _make_relay_service(
            config=HubRuntimeBridgeConfig(
                offline_queue_max=2,
                offline_queue_ttl_seconds=86400,
            )
        )
        svc.bind_agent_registry_writer(_make_writer())

        for i in range(3):
            event = RelayToHubEvent(
                type="user_message",
                room_id="room-1",
                agent_message_id=f"msg-{i}",
            )
            await svc.push_to_hub("hub-002", event)

        assert len(svc._facade._offline_queues["hub-002"]) == 2

    @pytest.mark.asyncio
    async def test_disconnect_preserves_pending_queue_events(self):
        svc = _make_relay_service()
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)
        stream = svc._facade.connect_hub_stream("hub-001")
        assert await stream.__anext__() == {"type": "connection_ready"}
        await svc.send_to_hub(
            HubDispatchCommand(
                hub_id="hub-001",
                agent_id="agent-1",
                local_agent_id="local-1",
                room_id="room-1",
                user_message_id="user-msg-1",
                agent_message_id="msg-1",
                payload={"text": "hello"},
                task_id="task-1",
            )
        )

        await stream.aclose()

        offline = svc._facade._offline_queues["hub-001"]
        assert len(offline) == 1
        assert offline.pop_fresh()[0]["agent_message_id"] == "msg-1"


@pytest.mark.asyncio
async def test_cancel_relay_task_uses_in_memory_live_queue_without_streams():
    svc = _make_relay_service()
    stream = svc._facade.connect_hub_stream("hub-001")
    assert await stream.__anext__() == {"type": "connection_ready"}

    delivered = await svc.cancel_relay_task(
        "hub-001",
        "agent-msg-1",
        "local-1",
        task_id="task-1",
    )

    event = await stream.__anext__()
    assert delivered is True
    assert event["type"] == "cancel_task"
    assert event["task_id"] == "task-1"
    await stream.aclose()


@pytest.mark.asyncio
async def test_reply_to_relay_task_uses_in_memory_live_queue_without_streams():
    svc = _make_relay_service()
    stream = svc._facade.connect_hub_stream("hub-001")
    assert await stream.__anext__() == {"type": "connection_ready"}

    delivered = await svc.reply_to_relay_task(
        "hub-001",
        "agent-msg-1",
        "local-1",
        "yes",
        "room-1",
        task_id="task-1",
        context_id="ctx-1",
    )

    event = await stream.__anext__()
    assert delivered is True
    assert event["type"] == "user_reply"
    assert event["reply_text"] == "yes"
    await stream.aclose()


@pytest.mark.asyncio
async def test_send_to_hub_uses_in_memory_live_queue_without_streams():
    svc = _make_relay_service()
    stream = svc._facade.connect_hub_stream("hub-001")
    assert await stream.__anext__() == {"type": "connection_ready"}

    result = await svc.send_to_hub(
        HubDispatchCommand(
            hub_id="hub-001",
            agent_id="agent-1",
            local_agent_id="local-1",
            room_id="room-1",
            user_message_id="user-msg-1",
            agent_message_id="agent-msg-1",
            payload={"text": "hello"},
            task_id="task-1",
        )
    )

    event = await stream.__anext__()
    assert result.accepted is True
    assert event["type"] == "user_message"
    assert event["task_id"] == "task-1"
    assert event["agent_message_id"] == "agent-msg-1"
    assert event["message"] == {"text": "hello"}
    await stream.aclose()


# ===========================================================================
# RelayService — Publish
# ===========================================================================


class TestRelayServicePublish:
    @pytest.mark.asyncio
    async def test_publish_rejects_wrong_hub_owner(self):
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-B"}

        key = _make_api_key(user_id="user-A")
        request = HubPublishRequest(room_id="room-1", events=[])
        with pytest.raises(PermissionError, match="not owned"):
            await svc.process_publish("hub-001", request, key)

    @pytest.mark.asyncio
    async def test_publish_rejects_wrong_room_owner(self):
        mongo = MagicMock()
        mongo.get_hub = AsyncMock(
            return_value={"hub_id": "hub-001", "user_id": "user-A"}
        )
        db_service = MagicMock()
        room = Room(
            room_id="room-1",
            room_name="R",
            room_owner_id="user-B",
            room_owner_name="B",
        )
        db_service.get_room_by_room_id = AsyncMock(return_value=room)

        svc = _make_relay_service(mongo=mongo, db_service=db_service)

        key = _make_api_key(user_id="user-A")
        request = HubPublishRequest(room_id="room-1", events=[])
        with pytest.raises(PermissionError, match="owner"):
            await svc.process_publish("hub-001", request, key)

    @pytest.mark.asyncio
    async def test_publish_agent_response_updates_message(self):
        mongo = MagicMock()
        mongo.get_hub = AsyncMock(
            return_value={"hub_id": "hub-001", "user_id": "user-001"}
        )
        db_service = MagicMock()
        room = Room(
            room_id="room-1",
            room_name="R",
            room_owner_id="user-001",
            room_owner_name="U",
        )
        db_service.get_room_by_room_id = AsyncMock(return_value=room)

        agent_msg = RoomAgentMessage(
            room_id="room-1",
            message_id="amsg-001",
            agent_id="agent-001",
            related_message_id="umsg-001",
            message_content=MessageContent(message_text=""),
        )
        db_service.get_room_agent_message_by_message_id = AsyncMock(
            return_value=agent_msg
        )
        db_service.update_room_agent_message_by_message_id = AsyncMock(
            return_value=True
        )
        db_service.update_task_state_on_message = AsyncMock(return_value=(True, None))
        db_service.is_message_cancelled = AsyncMock(return_value=False)
        agent_mock = MagicMock()
        agent_mock.hub_id = "hub-001"
        db_service.get_agent_by_agent_id = AsyncMock(return_value=agent_mock)

        sse = MagicMock()
        sse.send_agent_response = AsyncMock()
        sse.send_task_submitted = AsyncMock()
        sse.send_processing_status = AsyncMock()

        svc = _make_relay_service(mongo=mongo, db_service=db_service, sse_manager=sse)

        events = [
            HubPublishEvent(
                type="agent_response",
                agent_message_id="amsg-001",
                data={"content": "Hello from hub!"},
            )
        ]
        request = HubPublishRequest(room_id="room-1", events=events)
        key = _make_api_key(user_id="user-001")

        await svc.process_publish("hub-001", request, key)

        handler = svc.relay_transport.response_handler
        handler.handle.assert_awaited_once()
        event = handler.handle.call_args[0][0]
        assert event.kind == "response"
        assert event.text == "Hello from hub!"


# ===========================================================================
# RelayService — Hub Status
# ===========================================================================


class TestRelayServiceStatus:
    @pytest.mark.asyncio
    async def test_status_returns_hubs(self):
        svc = _make_relay_service()
        svc._mongo.get_hubs_by_user.return_value = [
            {"hub_id": "hub-001", "is_online": True, "last_connected_at": None},
        ]
        svc._mongo.count_hub_agents.return_value = (3, 1)

        result = await svc.get_hub_status("user-001")
        assert len(result) == 1
        assert result[0].hub_id == "hub-001"
        assert result[0].is_online is False
        assert result[0].last_connected_at is None
        assert result[0].agent_count == 4
        assert result[0].active_agent_count == 3
        assert result[0].inactive_agent_count == 1
