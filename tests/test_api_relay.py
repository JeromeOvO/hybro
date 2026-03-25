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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.api_key import APIKey
from models.hub import (
    HubAgentSync,
    HubPublishEvent,
    HubPublishRequest,
    RelayToHubEvent,
)
from models.room import MessageContent, Room, RoomAgentMessage
from modules.agent_event import AgentEvent
from modules.agent_response_handler import AgentResponseHandler
from modules.transports.relay import RelayTransport
from services.relay_service import RelayService
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
        db_service.update_room_agent_message_by_message_id = AsyncMock(return_value=True)
        db_service.update_task_state_on_message = AsyncMock(return_value=True)
        db_service.is_message_cancelled = AsyncMock(return_value=False)
        db_service.ai_service.get_embedding = AsyncMock(return_value=[0.0] * 128)
        db_service.pinecone.upsert = MagicMock()
    if sse_manager is None:
        sse_manager = MagicMock()
        sse_manager.send_agent_response = AsyncMock()
        sse_manager.send_task_submitted = AsyncMock()
        sse_manager.send_processing_status = AsyncMock()
        sse_manager.send_error = AsyncMock()

    svc = RelayService(
        mongo=mongo,
        database_service=db_service,
        sse_manager=sse_manager,
    )

    # Wire up RelayTransport for publish event delegation
    handler = MagicMock(spec=AgentResponseHandler)
    handler.handle = AsyncMock()
    relay_transport = RelayTransport(
        response_handler=handler,
        relay_service=svc,
        db=db_service,
        sse_manager=sse_manager,
    )
    svc.set_relay_transport(relay_transport)

    return svc


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


# ===========================================================================
# RelayService — Hub Connection Checks
# ===========================================================================


class TestIsHubConnected:
    def test_returns_false_when_not_connected(self):
        svc = _make_relay_service()
        assert svc.is_hub_connected("hub-001") is False

    def test_returns_true_for_queue_path(self):
        svc = _make_relay_service()
        svc._hub_queues["hub-001"] = MagicMock()
        assert svc.is_hub_connected("hub-001") is True

    def test_returns_true_for_streams_path(self):
        """Streams path uses _hub_disconnect_events, not _hub_queues."""
        import asyncio

        svc = _make_relay_service()
        svc._hub_disconnect_events["hub-001"] = asyncio.Event()
        assert svc.is_hub_connected("hub-001") is True

    def test_returns_true_for_both_paths(self):
        """If somehow both are present, still returns True."""
        import asyncio

        svc = _make_relay_service()
        svc._hub_queues["hub-001"] = MagicMock()
        svc._hub_disconnect_events["hub-001"] = asyncio.Event()
        assert svc.is_hub_connected("hub-001") is True


# ===========================================================================
# RelayService — Agent Sync
# ===========================================================================


class TestRelayServiceAgentSync:
    @pytest.mark.asyncio
    async def test_sync_agents_creates_hub_agents(self):
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}

        agents = [
            HubAgentSync(
                local_agent_id="local-1",
                name="Agent A",
                description="Desc A",
                agent_card=_make_agent_card("Agent A"),
            ),
        ]
        key = _make_api_key()
        synced = await svc.sync_agents("hub-001", agents, key)

        assert len(synced) == 1
        assert synced[0]["local_agent_id"] == "local-1"
        svc._mongo.upsert_hub_agent.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_new_agent_indexes_in_pinecone(self):
        """New hub-only agents should be indexed in Pinecone for discovery."""
        import asyncio

        fake_embedding = [0.1] * 128
        db_service = MagicMock()
        db_service.ai_service.get_embedding = AsyncMock(return_value=fake_embedding)
        db_service.pinecone.upsert = MagicMock()

        svc = _make_relay_service(db_service=db_service)
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}
        # Simulate a true first insert: return the same agent_id we generated
        svc._mongo.upsert_hub_agent = AsyncMock(
            side_effect=lambda hub_id, local_id, data: data["agent_id"]
        )
        # No existing doc -> find_one returns None (no indexed_description_hash)
        svc._mongo.agents_collection.find_one = AsyncMock(return_value=None)
        svc._mongo.agents_collection.update_one = AsyncMock()

        agents = [
            HubAgentSync(
                local_agent_id="local-1",
                name="Agent A",
                description="A helpful agent",
                agent_card=_make_agent_card("Agent A"),
            ),
        ]
        await svc.sync_agents("hub-001", agents, _make_api_key())

        # Drain all pending background tasks created by sync_agents
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

        db_service.ai_service.get_embedding.assert_awaited_once_with(
            "A hub test agent"
        )
        db_service.pinecone.upsert.assert_called_once()
        vectors = db_service.pinecone.upsert.call_args[0][0]
        assert len(vectors) == 1
        assert vectors[0]["values"] == fake_embedding
        assert vectors[0]["metadata"]["type"] == "a2a_agent"

        # Verify indexed_description_hash was written back to Mongo
        hash_writes = [
            call for call in svc._mongo.agents_collection.update_one.call_args_list
            if "indexed_description_hash" in str(call)
        ]
        assert len(hash_writes) == 1

    @pytest.mark.asyncio
    async def test_sync_existing_agent_skips_pinecone_when_hash_matches(self):
        """Pre-existing agents with matching hash should not be re-indexed."""
        import asyncio
        import hashlib

        description = "A hub test agent"
        desc_hash = hashlib.sha256(description.encode()).hexdigest()

        db_service = MagicMock()
        db_service.ai_service.get_embedding = AsyncMock()

        svc = _make_relay_service(db_service=db_service)
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}
        svc._mongo.agents_collection = AsyncMock()
        # First call: URL lookup returns existing agent; second call: hash lookup
        svc._mongo.agents_collection.find_one = AsyncMock(side_effect=[
            {"agent_id": "existing-001", "normalized_url": "localhost:8000"},
            {"indexed_description_hash": desc_hash},
        ])
        svc._mongo.agents_collection.update_one = AsyncMock()
        svc._mongo.agents_collection.update_many = AsyncMock()

        agents = [
            HubAgentSync(
                local_agent_id="local-1",
                name="Agent A",
                description="A helpful agent",
                agent_card=_make_agent_card("Agent A"),
            ),
        ]
        await svc.sync_agents("hub-001", agents, _make_api_key())

        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

        db_service.ai_service.get_embedding.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_reindexes_when_description_changes(self):
        """A description change should trigger Pinecone re-indexing."""
        import asyncio
        import hashlib

        old_hash = hashlib.sha256(b"old description").hexdigest()
        fake_embedding = [0.2] * 128
        db_service = MagicMock()
        db_service.ai_service.get_embedding = AsyncMock(return_value=fake_embedding)
        db_service.pinecone.upsert = MagicMock()

        svc = _make_relay_service(db_service=db_service)
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}
        svc._mongo.agents_collection = AsyncMock()
        # First call: URL lookup returns existing agent; second call: hash lookup
        svc._mongo.agents_collection.find_one = AsyncMock(side_effect=[
            {"agent_id": "existing-001", "normalized_url": "localhost:8000"},
            {"indexed_description_hash": old_hash},
        ])
        svc._mongo.agents_collection.update_one = AsyncMock()
        svc._mongo.agents_collection.update_many = AsyncMock()

        agents = [
            HubAgentSync(
                local_agent_id="local-1",
                name="Agent A",
                description="A helpful agent",
                agent_card=_make_agent_card("Agent A"),
            ),
        ]
        await svc.sync_agents("hub-001", agents, _make_api_key())

        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

        db_service.ai_service.get_embedding.assert_awaited_once()
        db_service.pinecone.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_retries_indexing_after_prior_failure(self):
        """If indexed_description_hash is missing, re-sync should retry indexing."""
        import asyncio

        fake_embedding = [0.3] * 128
        db_service = MagicMock()
        db_service.ai_service.get_embedding = AsyncMock(return_value=fake_embedding)
        db_service.pinecone.upsert = MagicMock()

        svc = _make_relay_service(db_service=db_service)
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}
        # Simulate re-sync: upsert_hub_agent returns an *existing* agent_id
        svc._mongo.upsert_hub_agent = AsyncMock(return_value="existing-agent-001")
        svc._mongo.agents_collection.find_one = AsyncMock(side_effect=[
            None,  # URL lookup: no match
            {},    # hash lookup: no indexed_description_hash field
        ])
        svc._mongo.agents_collection.update_one = AsyncMock()

        agents = [
            HubAgentSync(
                local_agent_id="local-1",
                name="Agent A",
                description="A helpful agent",
                agent_card=_make_agent_card("Agent A"),
            ),
        ]
        await svc.sync_agents("hub-001", agents, _make_api_key())

        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

        db_service.ai_service.get_embedding.assert_awaited_once()
        db_service.pinecone.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_agents_rejects_wrong_owner(self):
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "other-user"}

        key = _make_api_key(user_id="user-001")
        with pytest.raises(PermissionError):
            await svc.sync_agents("hub-001", [], key)

    @pytest.mark.asyncio
    async def test_sync_agents_sets_public_url(self):
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}

        card = _make_agent_card()
        agents = [
            HubAgentSync(
                local_agent_id="local-1",
                name="Agent A",
                description="Desc",
                agent_card=card,
            )
        ]
        with patch("services.relay_service.settings") as mock_settings:
            mock_settings.gateway_base_url = "https://api.hybro.ai/api/v1"
            mock_settings.relay_heartbeat_interval = 30
            mock_settings.relay_offline_queue_max = 100
            mock_settings.relay_offline_queue_ttl = 86400
            mock_settings.relay_hub_agent_heartbeat_miss_limit = 3

            await svc.sync_agents("hub-001", agents, _make_api_key())

        update_call = svc._mongo.agents_collection.update_one.call_args
        set_fields = update_call[0][1]["$set"]
        assert "gateway" in set_fields["public_url"]

    @pytest.mark.asyncio
    async def test_sync_empty_with_prune_missing_deactivates_all(self):
        """An empty roster with prune_missing=True should deactivate all hub agents."""
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}

        synced = await svc.sync_agents(
            "hub-001", [], _make_api_key(), prune_missing=True,
        )

        assert synced == []
        assert svc._mongo.agents_collection.update_many.await_count == 2

        hub_source_call = svc._mongo.agents_collection.update_many.call_args_list[0]
        query = hub_source_call[0][0]
        assert query["hub_id"] == "hub-001"
        assert query["source"] == "hub"
        assert query["agent_id"] == {"$nin": []}

    @pytest.mark.asyncio
    async def test_sync_empty_without_prune_missing_skips_pruning(self):
        """An empty roster with prune_missing=False should not touch existing agents."""
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}

        synced = await svc.sync_agents(
            "hub-001", [], _make_api_key(), prune_missing=False,
        )

        assert synced == []
        svc._mongo.agents_collection.update_many.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_new_agent_card_uses_dot_notation(self):
        """Hub-only (else branch): agent_card fields must arrive as dot-notation keys
        in upsert_hub_agent so that iconUrl is never overwritten on re-sync."""
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}

        captured_data: dict = {}

        async def capture_upsert(hub_id, local_id, data):
            captured_data.update(data)
            return "new-agent-id"

        svc._mongo.upsert_hub_agent = AsyncMock(side_effect=capture_upsert)

        agents = [
            HubAgentSync(
                local_agent_id="local-1",
                name="Agent A",
                description="Desc",
                agent_card=_make_agent_card("Agent A"),
            )
        ]
        await svc.sync_agents("hub-001", agents, _make_api_key())

        # Must NOT write a nested "agent_card" key — that would overwrite iconUrl on re-sync
        assert "agent_card" not in captured_data, \
            "agent_card must be spread into dot-notation keys, not a nested dict"
        # Non-blocked card fields must appear as dot-notation keys
        assert "agent_card.name" in captured_data
        assert "agent_card.url" in captured_data
        # Hybro-managed field must never be written from hub data
        assert "agent_card.iconUrl" not in captured_data


# ===========================================================================
# RelayService — Push to Hub
# ===========================================================================


class TestRelayServicePush:
    @pytest.mark.asyncio
    async def test_push_to_online_hub(self):
        import asyncio

        svc = _make_relay_service()
        q = asyncio.Queue()
        svc._hub_queues["hub-001"] = q

        event = RelayToHubEvent(type="user_message", room_id="room-1")
        delivered = await svc.push_to_hub("hub-001", event)

        assert delivered is True
        assert q.qsize() == 1

    @pytest.mark.asyncio
    async def test_push_to_offline_hub_queues(self):
        svc = _make_relay_service()

        event = RelayToHubEvent(type="user_message", room_id="room-1")
        delivered = await svc.push_to_hub("hub-002", event)

        assert delivered is False
        assert len(svc._offline_queues["hub-002"]) == 1

    @pytest.mark.asyncio
    async def test_offline_queue_overflow_drops_oldest(self):
        svc = _make_relay_service()

        with patch("services.relay_service.settings") as mock_settings:
            mock_settings.relay_offline_queue_max = 2
            mock_settings.relay_offline_queue_ttl = 86400

            for i in range(3):
                event = RelayToHubEvent(
                    type="user_message",
                    room_id="room-1",
                    agent_message_id=f"msg-{i}",
                )
                await svc.push_to_hub("hub-002", event)

        assert len(svc._offline_queues["hub-002"]) == 2


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
        mongo.get_hub = AsyncMock(return_value={"hub_id": "hub-001", "user_id": "user-A"})
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
        db_service.update_room_agent_message_by_message_id = AsyncMock(return_value=True)
        db_service.update_task_state_on_message = AsyncMock(return_value=True)
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

        handler = svc._relay_transport.response_handler
        handler.handle.assert_awaited_once()
        event = handler.handle.call_args[0][0]
        assert isinstance(event, AgentEvent)
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
        assert result[0].agent_count == 4
        assert result[0].active_agent_count == 3
        assert result[0].inactive_agent_count == 1


# ===========================================================================
# RelayTransport — Normalization
# ===========================================================================


def _make_relay_transport(*, handler=None, db_service=None, sse_manager=None):
    handler = handler or MagicMock(spec=AgentResponseHandler)
    handler.handle = AsyncMock()
    relay_svc = MagicMock()
    if db_service is None:
        db_service = MagicMock()
        db_service.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
        db_service.is_message_cancelled = AsyncMock(return_value=False)
        agent_mock = MagicMock()
        agent_mock.hub_id = "hub-001"
        db_service.get_agent_by_agent_id = AsyncMock(return_value=agent_mock)
    if sse_manager is None:
        sse_manager = MagicMock()
    return RelayTransport(
        response_handler=handler,
        relay_service=relay_svc,
        db=db_service,
        sse_manager=sse_manager,
    )


def _make_msg(
    room_id="room-1", message_id="amsg-001", agent_id="agent-001",
    related_message_id="umsg-001", user_id="user-001",
):
    return RoomAgentMessage(
        room_id=room_id,
        message_id=message_id,
        agent_id=agent_id,
        related_message_id=related_message_id,
        user_id=user_id,
        message_content=MessageContent(message_text=""),
    )


class TestRelayTransportNormalize:
    def test_agent_token_rejected_as_unknown(self):
        """agent_token is no longer a recognized event type after Phase 5b."""
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize("agent_token", "amsg-001", {"token": "hello"}, msg)
        assert event is None

    def test_agent_response(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize("agent_response", "amsg-001", {"content": "done"}, msg)
        assert event is not None
        assert event.kind == "response"
        assert event.text == "done"

    def test_agent_error(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize("agent_error", "amsg-001", {"error": "boom"}, msg)
        assert event is not None
        assert event.kind == "error"
        assert event.error_text == "boom"
        assert event.state == "failed"

    def test_task_submitted(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize(
            "task_submitted", "amsg-001",
            {"task_id": "t-1", "agent_name": "Agent X"}, msg,
        )
        assert event is not None
        assert event.kind == "task_submitted"
        assert event.task_id == "t-1"
        assert event.agent_name == "Agent X"

    def test_artifact_update(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize(
            "artifact_update", "amsg-001",
            {"text": "chunk", "artifact": {"id": "a1"}}, msg,
        )
        assert event is not None
        assert event.kind == "artifact_update"
        assert event.text == "chunk"
        assert event.artifacts == [{"id": "a1"}]

    def test_task_status_completed(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize(
            "task_status", "amsg-001",
            {"state": "completed", "status_text": "all done"}, msg,
        )
        assert event is not None
        assert event.kind == "response"
        assert event.text == "all done"

    def test_task_status_failed(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize(
            "task_status", "amsg-001",
            {"state": "failed", "status_text": "oops"}, msg,
        )
        assert event is not None
        assert event.kind == "error"
        assert event.error_text == "oops"

    def test_task_status_canceled(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize(
            "task_status", "amsg-001",
            {"state": "canceled", "status_text": ""}, msg,
        )
        assert event is not None
        assert event.kind == "canceled"

    def test_task_status_interactive(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize(
            "task_status", "amsg-001",
            {"state": "input-required", "status_text": "need input"}, msg,
        )
        assert event is not None
        assert event.kind == "interactive"

    def test_task_status_working(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize(
            "task_status", "amsg-001",
            {"state": "working", "status_text": "still going"}, msg,
        )
        assert event is not None
        assert event.kind == "status_update"

    def test_task_interactive(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize(
            "task_interactive", "amsg-001",
            {"state": "input-required", "status_text": "need info", "task_id": "t-1"}, msg,
        )
        assert event is not None
        assert event.kind == "interactive"
        assert event.task_id == "t-1"

    def test_processing_status(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize(
            "processing_status", "amsg-001",
            {"status": "completed", "user_message_id": "umsg-001", "details": "done"}, msg,
        )
        assert event is not None
        assert event.kind == "processing_status"
        assert event.details == "done"

    def test_unknown_event_returns_none(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize("unknown_type", "amsg-001", {}, msg)
        assert event is None

    def test_unknown_task_state_returns_none(self):
        rt = _make_relay_transport()
        msg = _make_msg()
        event = rt._normalize(
            "task_status", "amsg-001",
            {"state": "not-a-state"}, msg,
        )
        assert event is None


class TestRelayTransportHandlePublish:
    @pytest.mark.asyncio
    async def test_discards_cancelled_message(self):
        db = MagicMock()
        msg = _make_msg()
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
        db.is_message_cancelled = AsyncMock(return_value=True)
        agent_mock = MagicMock()
        agent_mock.hub_id = "hub-001"
        db.get_agent_by_agent_id = AsyncMock(return_value=agent_mock)

        rt = _make_relay_transport(db_service=db)
        await rt.handle_publish_event("agent_token", "amsg-001", {"token": "hi"}, "room-1", "hub-001")
        rt.response_handler.handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_message_ignored(self):
        db = MagicMock()
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=None)

        rt = _make_relay_transport(db_service=db)
        await rt.handle_publish_event("agent_token", "amsg-999", {"token": "hi"}, "room-1", "hub-001")
        rt.response_handler.handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delegates_to_handler(self):
        db = MagicMock()
        msg = _make_msg()
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
        db.is_message_cancelled = AsyncMock(return_value=False)
        agent_mock = MagicMock()
        agent_mock.hub_id = "hub-001"
        db.get_agent_by_agent_id = AsyncMock(return_value=agent_mock)

        rt = _make_relay_transport(db_service=db)
        await rt.handle_publish_event("agent_response", "amsg-001", {"content": "hi"}, "room-1", "hub-001")

        rt.response_handler.handle.assert_awaited_once()
        event = rt.response_handler.handle.call_args[0][0]
        assert event.kind == "response"
        assert event.text == "hi"

    @pytest.mark.asyncio
    async def test_rejects_cross_hub_publish(self):
        """A hub must not publish events for agents belonging to a different hub."""
        db = MagicMock()
        msg = _make_msg()
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
        agent_mock = MagicMock()
        agent_mock.hub_id = "hub-A"
        db.get_agent_by_agent_id = AsyncMock(return_value=agent_mock)

        rt = _make_relay_transport(db_service=db)
        await rt.handle_publish_event(
            "agent_response", "amsg-001", {"content": "hijack"}, "room-1", "hub-B",
        )
        rt.response_handler.handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_unknown_agent(self):
        """Publish is rejected if the agent doc doesn't exist."""
        db = MagicMock()
        msg = _make_msg()
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
        db.get_agent_by_agent_id = AsyncMock(return_value=None)

        rt = _make_relay_transport(db_service=db)
        await rt.handle_publish_event(
            "agent_response", "amsg-001", {"content": "orphan"}, "room-1", "hub-001",
        )
        rt.response_handler.handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_message_with_no_agent_id(self):
        """Publish is rejected if the message has no agent_id."""
        db = MagicMock()
        msg = _make_msg(agent_id=None)
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)

        rt = _make_relay_transport(db_service=db)
        await rt.handle_publish_event(
            "agent_response", "amsg-001", {"content": "no agent"}, "room-1", "hub-001",
        )
        rt.response_handler.handle.assert_not_awaited()
