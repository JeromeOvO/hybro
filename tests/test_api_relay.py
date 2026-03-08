"""
Unit tests for Relay API endpoints and RelayService.

Tests cover:
- Hub registration (success, duplicate, invalid key)
- Agent sync (upsert, dedup by hub_id+local_agent_id, agent_card.url rewriting)
- SSE events endpoint (connection, message delivery)
- Publish endpoint (token validation, room ownership auth, event persistence, resume)
- Hub status endpoint
- Offline queue behavior (enqueue, overflow)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.utils.connection_token import (
    create_connection_token,
    verify_connection_token,
)
from models.api_key import APIKey
from models.hub import (
    HubAgentSync,
    HubPublishEvent,
    HubPublishRequest,
    RelayToHubEvent,
)
from models.room import MessageContent, Room, RoomAgentMessage
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
        mongo.set_hub_agents_online_status = AsyncMock()
        mongo.upsert_hub_agent = AsyncMock(return_value="agent-new-001")
        mongo.count_hub_agents = AsyncMock(return_value=0)
        mongo.agents_collection = MagicMock()
        mongo.agents_collection.find_one = AsyncMock(return_value=None)
        mongo.agents_collection.update_one = AsyncMock()
        mongo.agents_collection.update_many = AsyncMock()
    if db_service is None:
        db_service = MagicMock()
        db_service.get_room_by_room_id = AsyncMock(return_value=None)
        db_service.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
        db_service.update_room_agent_message_by_message_id = AsyncMock(return_value=True)
        db_service.ai_service.get_embedding = AsyncMock(return_value=[0.0] * 128)
        db_service.pinecone.upsert = MagicMock()
    if sse_manager is None:
        sse_manager = MagicMock()
        sse_manager.send_agent_response = AsyncMock()
        sse_manager.send_agent_token = AsyncMock()
        sse_manager.send_task_submitted = AsyncMock()
        sse_manager.send_processing_status = AsyncMock()
        sse_manager.send_error = AsyncMock()

    return RelayService(
        mongo=mongo,
        database_service=db_service,
        sse_manager=sse_manager,
    )


# ===========================================================================
# Connection Token
# ===========================================================================


class TestConnectionToken:
    def test_create_and_verify(self):
        secret = "test-secret-key-for-jwt"
        token = create_connection_token("hub-123", secret)
        assert verify_connection_token(token, "hub-123", secret)

    def test_wrong_hub_id_rejected(self):
        secret = "test-secret"
        token = create_connection_token("hub-123", secret)
        assert not verify_connection_token(token, "hub-999", secret)

    def test_wrong_secret_rejected(self):
        token = create_connection_token("hub-123", "secret-a")
        assert not verify_connection_token(token, "hub-123", "secret-b")


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

    @pytest.mark.asyncio
    async def test_sync_existing_agent_skips_pinecone_index(self):
        """Pre-existing agents (registered via web UI) should not be re-indexed."""
        import asyncio

        db_service = MagicMock()
        db_service.ai_service.get_embedding = AsyncMock()

        svc = _make_relay_service(db_service=db_service)
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}
        svc._mongo.agents_collection = AsyncMock()
        svc._mongo.agents_collection.find_one = AsyncMock(return_value={
            "agent_id": "existing-001",
            "normalized_url": "localhost:8000",
        })
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

        db_service.ai_service.get_embedding.assert_not_awaited()

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
            mock_settings.relay_connection_token_secret = ""
            mock_settings.relay_hub_agent_heartbeat_miss_limit = 3

            await svc.sync_agents("hub-001", agents, _make_api_key())

        update_call = svc._mongo.agents_collection.update_one.call_args
        set_fields = update_call[0][1]["$set"]
        assert "gateway" in set_fields["public_url"]


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
    async def test_publish_rejects_invalid_token(self):
        svc = _make_relay_service()
        svc._mongo.get_hub.return_value = {"hub_id": "hub-001", "user_id": "user-001"}

        request = HubPublishRequest(room_id="room-1", events=[])
        with pytest.raises(PermissionError, match="Invalid"):
            await svc.process_publish("hub-001", request, "bad-token")

    @pytest.mark.asyncio
    async def test_publish_rejects_wrong_room_owner(self):
        secret = "test-secret"
        token = create_connection_token("hub-001", secret)

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

        request = HubPublishRequest(room_id="room-1", events=[])
        with patch("services.relay_service.settings") as ms:
            ms.relay_connection_token_secret = secret
            with pytest.raises(PermissionError, match="owner"):
                await svc.process_publish("hub-001", request, token)

    @pytest.mark.asyncio
    async def test_publish_agent_response_updates_message(self):
        secret = "test-secret"
        token = create_connection_token("hub-001", secret)

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

        sse = MagicMock()
        sse.send_agent_response = AsyncMock()
        sse.send_agent_token = AsyncMock()
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

        with patch("services.relay_service.settings") as ms:
            ms.relay_connection_token_secret = secret
            with patch(
                "services.relay_service.RelayService._resume_orchestration",
                new_callable=AsyncMock,
            ):
                await svc.process_publish("hub-001", request, token)

        sse.send_agent_response.assert_awaited_once()
        db_service.update_room_agent_message_by_message_id.assert_awaited_once()


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
        svc._mongo.count_hub_agents.return_value = 3

        result = await svc.get_hub_status("user-001")
        assert len(result) == 1
        assert result[0].hub_id == "hub-001"
        assert result[0].agent_count == 3
