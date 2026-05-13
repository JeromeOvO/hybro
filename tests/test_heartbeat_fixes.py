"""Tests for hub heartbeat fixes: SSE refresh, self-heal, connection guard, ownership check."""

import asyncio
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models.api_key import APIKey
from models.hub import HubAgentSync, RelayToHubEvent
from services.agent_liveness_service import (
    bind_agent_liveness_deps,
    check_and_sync_liveness,
    reset_agent_liveness_deps,
)
from services.relay_service import (
    RelayHubLivenessReader,
    RelayService,
)
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


def _make_mongo():
    mongo = MagicMock()
    mongo.upsert_hub = AsyncMock()
    mongo.get_hub = AsyncMock(return_value={"hub_id": "hub-1", "user_id": "user-001"})
    mongo.get_hubs_by_user = AsyncMock(return_value=[])
    mongo.update_hub_status = AsyncMock()
    mongo.update_hub_status_if_current = AsyncMock(return_value=True)
    mongo.upsert_hub_agent = AsyncMock(return_value="agent-new-001")
    mongo.count_hub_agents = AsyncMock(return_value=(0, 0))
    mongo.agents_collection = MagicMock()
    mongo.agents_collection.find_one = AsyncMock(return_value=None)
    mongo.agents_collection.update_one = AsyncMock()
    mongo.agents_collection.update_many = AsyncMock()
    mongo.hubs_collection = MagicMock()
    return mongo


def _make_streams():
    streams = MagicMock()
    streams.record_heartbeat = AsyncMock()
    streams.is_hub_alive = AsyncMock(return_value=True)
    streams.read_events = AsyncMock(return_value=[])
    streams.push_event = AsyncMock(return_value="1-0")
    return streams


def _make_writer():
    writer = MagicMock()
    writer.mark_hub_agents_offline = AsyncMock()
    return writer


class SyncHubLivenessReader:
    def is_hub_online(self, hub_id: str) -> bool:
        return True

    async def get_hub_owner_id(self, hub_id: str) -> str | None:
        return "user-001"


class FakeHubLivenessReader:
    def __init__(self, online: bool) -> None:
        self.online = online
        self.checked: list[str] = []

    async def is_hub_online(self, hub_id: str) -> bool:
        self.checked.append(hub_id)
        return self.online

    async def get_hub_owner_id(self, hub_id: str) -> str | None:
        return "user-001"


def _make_service(mongo=None, streams=None):
    if mongo is None:
        mongo = _make_mongo()

    db_service = MagicMock()
    db_service.get_room_by_room_id = AsyncMock(return_value=None)
    db_service.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
    db_service.update_room_agent_message_by_message_id = AsyncMock(return_value=True)
    db_service.update_task_state_on_message = AsyncMock(return_value=True)
    db_service.is_message_cancelled = AsyncMock(return_value=False)
    db_service.ai_service.get_embedding = AsyncMock(return_value=[0.0] * 128)
    db_service.pinecone.upsert = MagicMock()

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
    if streams is not None:
        svc.set_stream_service(streams)
    return svc


# ===========================================================================
# is_hub_alive — authoritative liveness check
# ===========================================================================


@pytest.mark.asyncio
class TestIsHubAlive:
    async def test_streams_path_delegates_to_redis(self):
        """With Streams, is_hub_alive delegates to Redis is_hub_alive."""
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=True)
        svc = _make_service(streams=streams)

        assert await svc.is_hub_alive("hub-1") is True
        streams.is_hub_alive.assert_awaited_once_with("hub-1")

    async def test_streams_path_returns_false_when_redis_dead(self):
        """With Streams, returns False when Redis key is absent."""
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=False)
        svc = _make_service(streams=streams)

        assert await svc.is_hub_alive("hub-1") is False

    async def test_in_memory_path_uses_local_state(self):
        """Without Streams, falls back to process-local connection check."""
        svc = _make_service(streams=None)
        assert await svc.is_hub_alive("hub-1") is False

        svc._hub_queues["hub-1"] = asyncio.Queue()
        assert await svc.is_hub_alive("hub-1") is True

    async def test_liveness_reader_delegates_to_authoritative_relay_liveness(self):
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=True)
        svc = _make_service(streams=streams)
        svc.get_hub_owner_id = AsyncMock(return_value="user-001")
        reader = RelayHubLivenessReader(svc)

        assert asyncio.iscoroutinefunction(reader.is_hub_online)
        assert await reader.is_hub_online("hub-1") is True
        assert await reader.get_hub_owner_id("hub-1") == "user-001"
        streams.is_hub_alive.assert_awaited_once_with("hub-1")
        svc.get_hub_owner_id.assert_awaited_once_with("hub-1")

    async def test_relay_get_hub_owner_id_uses_public_relay_method(self):
        mongo = _make_mongo()
        svc = _make_service(mongo=mongo)

        assert await svc.get_hub_owner_id("hub-1") == "user-001"
        mongo.get_hub.assert_awaited_once_with("hub-1")

    async def test_liveness_reader_in_memory_path_returns_bool(self):
        svc = _make_service(streams=None)
        reader = RelayHubLivenessReader(svc)

        assert await reader.is_hub_online("hub-1") is False
        svc._hub_queues["hub-1"] = asyncio.Queue()
        assert await reader.is_hub_online("hub-1") is True

    async def test_agent_liveness_bind_rejects_sync_liveness_reader(self):
        with pytest.raises(TypeError, match="is_hub_online must be async"):
            bind_agent_liveness_deps(
                hub_liveness_reader=SyncHubLivenessReader(),
                agent_registry_writer=_make_writer(),
            )

    async def test_agent_liveness_uses_async_liveness_reader(self):
        from types import SimpleNamespace
        from models.agent import AgentStatus

        reader = FakeHubLivenessReader(True)
        writer = _make_writer()
        bind_agent_liveness_deps(
            hub_liveness_reader=reader,
            agent_registry_writer=writer,
        )
        agent = SimpleNamespace(
            agent_id="agent-1",
            hub_id="hub-1",
            source="hub",
            agent_status=AgentStatus.active,
        )

        try:
            result = await check_and_sync_liveness(agent)
        finally:
            reset_agent_liveness_deps()

        assert result.agent_status == AgentStatus.active
        assert reader.checked == ["hub-1"]
        writer.mark_hub_agents_offline.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_agents_validates_cards_before_writer_path():
    svc = _make_service(streams=_make_streams())
    writer = MagicMock()
    writer.sync_hub_agents = AsyncMock(return_value=[])
    svc.bind_agent_registry_writer(writer)

    synced = await svc.sync_agents(
        "hub-1",
        [
            HubAgentSync(
                local_agent_id="bad-local",
                name="Bad",
                description="Missing required A2A card fields",
                agent_card={"name": "Bad"},
            )
        ],
        _make_api_key(),
    )

    assert synced == []
    writer.sync_hub_agents.assert_not_awaited()


# ===========================================================================
# get_hub_status — always consults authoritative liveness
# ===========================================================================


@pytest.mark.asyncio
class TestGetHubStatusLiveness:
    async def test_reports_online_when_redis_alive_but_mongo_offline(self):
        """get_hub_status reports online even if MongoDB says offline,
        because is_hub_alive checks Redis directly."""
        mongo = _make_mongo()
        mongo.get_hubs_by_user = AsyncMock(return_value=[
            {"hub_id": "hub-1", "is_online": False, "last_connected_at": None},
        ])
        mongo.count_hub_agents = AsyncMock(return_value=(2, 1))
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=True)
        svc = _make_service(mongo=mongo, streams=streams)

        result = await svc.get_hub_status("user-001")
        assert len(result) == 1
        assert result[0].is_online is True

    async def test_reports_offline_when_redis_dead_and_mongo_online(self):
        """get_hub_status reports offline when Redis key is absent,
        even if MongoDB still says online (stale)."""
        mongo = _make_mongo()
        mongo.get_hubs_by_user = AsyncMock(return_value=[
            {"hub_id": "hub-1", "is_online": True, "last_connected_at": None},
        ])
        mongo.count_hub_agents = AsyncMock(return_value=(0, 3))
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=False)
        svc = _make_service(mongo=mongo, streams=streams)

        result = await svc.get_hub_status("user-001")
        assert len(result) == 1
        assert result[0].is_online is False

    async def test_no_side_effects_on_read_path(self):
        """get_hub_status must NOT call mark_hub_agents_offline (read-only)."""
        mongo = _make_mongo()
        mongo.get_hubs_by_user = AsyncMock(return_value=[
            {"hub_id": "hub-1", "is_online": True, "last_connected_at": None},
        ])
        mongo.count_hub_agents = AsyncMock(return_value=(0, 0))
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=False)
        svc = _make_service(mongo=mongo, streams=streams)

        await svc.get_hub_status("user-001")
        mongo.update_hub_status.assert_not_awaited()
        mongo.update_hub_status_if_current.assert_not_awaited()


# ===========================================================================
# _do_heartbeat_check Pass 1 — connection_id guard
# ===========================================================================


@pytest.mark.asyncio
class TestHeartbeatCheckConnectionIdGuard:
    async def test_pass1_sends_connection_id_to_mark_offline(self):
        """Pass 1 passes the stored connection_id when marking hubs offline."""
        mongo = _make_mongo()
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=False)

        online_cursor = MagicMock()
        online_cursor.to_list = AsyncMock(return_value=[
            {"hub_id": "hub-stale", "connection_id": "conn-old-123"},
        ])
        offline_cursor = MagicMock()
        offline_cursor.to_list = AsyncMock(return_value=[])

        def find_router(query, projection):
            if query.get("is_online") is True:
                return online_cursor
            return offline_cursor

        mongo.hubs_collection.find = MagicMock(side_effect=find_router)
        svc = _make_service(mongo=mongo, streams=streams)
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)

        await svc._do_heartbeat_check(stale_threshold=90)

        mongo.update_hub_status_if_current.assert_awaited_with(
            "hub-stale", connection_id="conn-old-123", is_online=False,
        )
        writer.mark_hub_agents_offline.assert_awaited_once_with("hub-stale")

    async def test_pass1_skips_mark_if_connection_superseded(self):
        """If connection_id doesn't match (new connect_hub raced), skip offline mark."""
        mongo = _make_mongo()
        mongo.update_hub_status_if_current = AsyncMock(return_value=False)
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=False)

        online_cursor = MagicMock()
        online_cursor.to_list = AsyncMock(return_value=[
            {"hub_id": "hub-race", "connection_id": "conn-old"},
        ])
        offline_cursor = MagicMock()
        offline_cursor.to_list = AsyncMock(return_value=[])

        def find_router(query, projection):
            if query.get("is_online") is True:
                return online_cursor
            return offline_cursor

        mongo.hubs_collection.find = MagicMock(side_effect=find_router)
        svc = _make_service(mongo=mongo, streams=streams)

        await svc._do_heartbeat_check(stale_threshold=90)

        mongo.update_hub_status_if_current.assert_awaited_once()
        mongo.agents_collection.update_many.assert_not_awaited()

    async def test_pass1_logs_expiry_with_connection_and_local_context(self, caplog):
        mongo = _make_mongo()
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=False)

        online_cursor = MagicMock()
        online_cursor.to_list = AsyncMock(return_value=[
            {"hub_id": "hub-stale", "connection_id": "conn-old-123"},
        ])
        offline_cursor = MagicMock()
        offline_cursor.to_list = AsyncMock(return_value=[])

        def find_router(query, projection):
            if query.get("is_online") is True:
                return online_cursor
            return offline_cursor

        mongo.hubs_collection.find = MagicMock(side_effect=find_router)
        svc = _make_service(mongo=mongo, streams=streams)
        svc.bind_agent_registry_writer(_make_writer())
        svc._hub_disconnect_events["hub-stale"] = asyncio.Event()

        with caplog.at_level(logging.WARNING, logger="services.relay_service"):
            await svc._do_heartbeat_check(stale_threshold=90)

        assert "hub-stale" in caplog.text
        assert "connection_id=conn-old-123" in caplog.text
        assert "redis_alive=False" in caplog.text
        assert "local_disconnect_event=True" in caplog.text


@pytest.mark.asyncio
async def test_streams_push_logs_redis_dead_rejection(caplog):
    streams = _make_streams()
    streams.is_hub_alive = AsyncMock(return_value=False)
    svc = _make_service(streams=streams)
    event = RelayToHubEvent(
        type="user_message",
        room_id="room-1",
        agent_message_id="msg-1",
        local_agent_id="local-1",
    )

    with caplog.at_level(logging.WARNING, logger="services.relay_service"):
        delivered = await svc.push_to_hub("hub-dead", event)

    assert delivered is False
    assert "hub-dead" in caplog.text
    assert "event_type=user_message" in caplog.text
    assert "agent_message_id=msg-1" in caplog.text
    assert "redis_alive=False" in caplog.text


# ===========================================================================
# Fix 1: SSE loop refreshes heartbeat on every iteration
# ===========================================================================


@pytest.mark.asyncio
class TestSSEHeartbeatRefresh:
    async def test_connect_hub_refreshes_heartbeat_on_data(self):
        """When events arrive, the SSE loop refreshes the Redis heartbeat."""
        mongo = _make_mongo()
        streams = _make_streams()
        streams.read_events = AsyncMock(
            side_effect=[
                [("1-0", {"type": "task_dispatch"})],
                [],  # second call returns empty to trigger the else branch
            ]
        )
        svc = _make_service(mongo=mongo, streams=streams)
        api_key = _make_api_key()

        events = []
        gen = svc.connect_hub("hub-1", api_key)
        events.append(await gen.__anext__())  # connection_ready
        events.append(await gen.__anext__())  # first event (task_dispatch)

        assert streams.record_heartbeat.await_count >= 2  # initial + loop iteration
        await gen.aclose()

    async def test_connect_hub_refreshes_heartbeat_on_timeout(self):
        """Even when no events arrive (idle), the SSE loop refreshes heartbeat."""
        mongo = _make_mongo()
        streams = _make_streams()
        streams.read_events = AsyncMock(return_value=[])
        svc = _make_service(mongo=mongo, streams=streams)
        api_key = _make_api_key()

        gen = svc.connect_hub("hub-1", api_key)
        await gen.__anext__()  # connection_ready
        await gen.__anext__()  # heartbeat (timeout path)

        assert streams.record_heartbeat.await_count >= 2  # initial + loop
        await gen.aclose()


# ===========================================================================
# Fix 2: Self-healing in _do_heartbeat_check
# ===========================================================================


@pytest.mark.asyncio
class TestHeartbeatSelfHeal:
    async def test_heartbeat_check_recovers_offline_hub_with_alive_redis(self):
        """Hubs marked offline in MongoDB but alive in Redis get re-marked online."""
        mongo = _make_mongo()
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=True)

        online_cursor = MagicMock()
        online_cursor.to_list = AsyncMock(return_value=[])  # no stale online hubs
        offline_cursor = MagicMock()
        offline_cursor.to_list = AsyncMock(
            return_value=[{"hub_id": "hub-recovering"}]
        )

        def find_router(query, projection):
            if query.get("is_online") is True:
                return online_cursor
            return offline_cursor

        mongo.hubs_collection.find = MagicMock(side_effect=find_router)

        svc = _make_service(mongo=mongo, streams=streams)
        await svc._do_heartbeat_check(stale_threshold=90)

        mongo.update_hub_status.assert_awaited_with("hub-recovering", is_online=True)

    async def test_heartbeat_check_does_not_recover_truly_dead_hub(self):
        """Hubs marked offline that are also dead in Redis stay offline."""
        mongo = _make_mongo()
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=False)

        online_cursor = MagicMock()
        online_cursor.to_list = AsyncMock(return_value=[])
        offline_cursor = MagicMock()
        offline_cursor.to_list = AsyncMock(
            return_value=[{"hub_id": "hub-dead"}]
        )

        def find_router(query, projection):
            if query.get("is_online") is True:
                return online_cursor
            return offline_cursor

        mongo.hubs_collection.find = MagicMock(side_effect=find_router)

        svc = _make_service(mongo=mongo, streams=streams)
        await svc._do_heartbeat_check(stale_threshold=90)

        mongo.update_hub_status.assert_not_awaited()


# ===========================================================================
# Fix 2b: connection_id guard on mark_hub_agents_offline
# ===========================================================================


@pytest.mark.asyncio
class TestMarkHubAgentsOfflineGuard:
    async def test_unconditional_offline_without_connection_id(self):
        """Without connection_id, mark_hub_agents_offline is unconditional."""
        mongo = _make_mongo()
        svc = _make_service(mongo=mongo)
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)

        await svc.mark_hub_agents_offline("hub-1")

        mongo.update_hub_status.assert_awaited_with("hub-1", is_online=False)
        writer.mark_hub_agents_offline.assert_awaited_once_with("hub-1")
        mongo.agents_collection.update_many.assert_not_awaited()

    async def test_conditional_offline_with_matching_connection_id(self):
        """With matching connection_id, offline proceeds."""
        mongo = _make_mongo()
        mongo.update_hub_status_if_current = AsyncMock(return_value=True)
        svc = _make_service(mongo=mongo)
        writer = _make_writer()
        svc.bind_agent_registry_writer(writer)

        await svc.mark_hub_agents_offline("hub-1", connection_id="conn-123")

        mongo.update_hub_status_if_current.assert_awaited_with(
            "hub-1", connection_id="conn-123", is_online=False,
        )
        writer.mark_hub_agents_offline.assert_awaited_once_with("hub-1")
        mongo.agents_collection.update_many.assert_not_awaited()

    async def test_offline_requires_registry_writer_for_agent_status(self):
        """Hub status can be updated here, but agent status writes require the writer."""
        mongo = _make_mongo()
        svc = _make_service(mongo=mongo)

        with pytest.raises(RuntimeError, match="AgentRegistryWriter"):
            await svc.mark_hub_agents_offline("hub-1")

        mongo.update_hub_status.assert_awaited_with("hub-1", is_online=False)
        mongo.agents_collection.update_many.assert_not_awaited()

    async def test_skips_offline_when_connection_superseded(self):
        """When connection_id doesn't match (superseded), offline is skipped."""
        mongo = _make_mongo()
        mongo.update_hub_status_if_current = AsyncMock(return_value=False)
        svc = _make_service(mongo=mongo)

        await svc.mark_hub_agents_offline("hub-1", connection_id="old-conn")

        mongo.update_hub_status_if_current.assert_awaited_once()
        mongo.agents_collection.update_many.assert_not_awaited()


# ===========================================================================
# Fix 4: SSE disconnect on heartbeat expiry
# ===========================================================================


@pytest.mark.asyncio
class TestHeartbeatExpiryDisconnect:
    async def test_signals_disconnect_event_on_expiry(self):
        """When a hub's heartbeat expires, the disconnect event is signaled."""
        mongo = _make_mongo()
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=False)

        online_cursor = MagicMock()
        online_cursor.to_list = AsyncMock(
            return_value=[{"hub_id": "hub-stale", "connection_id": "conn-1"}]
        )
        offline_cursor = MagicMock()
        offline_cursor.to_list = AsyncMock(return_value=[])

        def find_router(query, projection):
            if query.get("is_online") is True:
                return online_cursor
            return offline_cursor

        mongo.hubs_collection.find = MagicMock(side_effect=find_router)

        svc = _make_service(mongo=mongo, streams=streams)
        svc.bind_agent_registry_writer(_make_writer())

        disconnect_event = asyncio.Event()
        svc._hub_disconnect_events["hub-stale"] = disconnect_event

        await svc._do_heartbeat_check(stale_threshold=90)

        assert disconnect_event.is_set()

    async def test_no_error_when_disconnect_event_missing(self):
        """No error if the hub has no local disconnect event (multi-instance)."""
        mongo = _make_mongo()
        streams = _make_streams()
        streams.is_hub_alive = AsyncMock(return_value=False)

        online_cursor = MagicMock()
        online_cursor.to_list = AsyncMock(
            return_value=[{"hub_id": "hub-remote", "connection_id": "conn-1"}]
        )
        offline_cursor = MagicMock()
        offline_cursor.to_list = AsyncMock(return_value=[])

        def find_router(query, projection):
            if query.get("is_online") is True:
                return online_cursor
            return offline_cursor

        mongo.hubs_collection.find = MagicMock(side_effect=find_router)

        svc = _make_service(mongo=mongo, streams=streams)
        svc.bind_agent_registry_writer(_make_writer())
        # No disconnect event registered for hub-remote
        await svc._do_heartbeat_check(stale_threshold=90)  # should not raise


# ===========================================================================
# Fix 5: Ownership check on record_hub_heartbeat (Streams path)
# ===========================================================================


@pytest.mark.asyncio
class TestHeartbeatOwnershipCheck:
    async def test_accepts_heartbeat_from_owner(self):
        """Heartbeat from the hub's owner succeeds."""
        mongo = _make_mongo()
        mongo.get_hub = AsyncMock(
            return_value={"hub_id": "hub-1", "user_id": "user-001"}
        )
        streams = _make_streams()
        svc = _make_service(mongo=mongo, streams=streams)
        api_key = _make_api_key(user_id="user-001")

        await svc.record_hub_heartbeat("hub-1", api_key)
        streams.record_heartbeat.assert_awaited_once_with("hub-1")

    async def test_rejects_heartbeat_from_non_owner(self):
        """Heartbeat from a different user is rejected."""
        mongo = _make_mongo()
        mongo.get_hub = AsyncMock(
            return_value={"hub_id": "hub-1", "user_id": "user-001"}
        )
        streams = _make_streams()
        svc = _make_service(mongo=mongo, streams=streams)
        api_key = _make_api_key(user_id="user-attacker")

        with pytest.raises(PermissionError, match="not owned"):
            await svc.record_hub_heartbeat("hub-1", api_key)

    async def test_rejected_heartbeat_logs_hub_owner_and_caller(self, caplog):
        mongo = _make_mongo()
        mongo.get_hub = AsyncMock(
            return_value={"hub_id": "hub-1", "user_id": "user-001"}
        )
        streams = _make_streams()
        svc = _make_service(mongo=mongo, streams=streams)
        api_key = _make_api_key(user_id="user-attacker")

        with caplog.at_level(logging.WARNING, logger="services.relay_service"):
            with pytest.raises(PermissionError, match="not owned"):
                await svc.record_hub_heartbeat("hub-1", api_key)

        assert "hub-1" in caplog.text
        assert "owner_id=user-001" in caplog.text
        assert "caller_user_id=user-attacker" in caplog.text
        assert "heartbeat rejected" in caplog.text

    async def test_rejects_heartbeat_for_nonexistent_hub(self):
        """Heartbeat for a hub that doesn't exist is rejected."""
        mongo = _make_mongo()
        mongo.get_hub = AsyncMock(return_value=None)
        streams = _make_streams()
        svc = _make_service(mongo=mongo, streams=streams)
        api_key = _make_api_key()

        with pytest.raises(PermissionError, match="not owned"):
            await svc.record_hub_heartbeat("hub-nonexistent", api_key)
