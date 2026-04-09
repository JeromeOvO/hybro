"""Tests for hub heartbeat fixes: SSE refresh, self-heal, connection guard, ownership check."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models.api_key import APIKey
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
    return streams


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

        await svc.mark_hub_agents_offline("hub-1")

        mongo.update_hub_status.assert_awaited_with("hub-1", is_online=False)
        mongo.agents_collection.update_many.assert_awaited_once()

    async def test_conditional_offline_with_matching_connection_id(self):
        """With matching connection_id, offline proceeds."""
        mongo = _make_mongo()
        mongo.update_hub_status_if_current = AsyncMock(return_value=True)
        svc = _make_service(mongo=mongo)

        await svc.mark_hub_agents_offline("hub-1", connection_id="conn-123")

        mongo.update_hub_status_if_current.assert_awaited_with(
            "hub-1", connection_id="conn-123", is_online=False,
        )
        mongo.agents_collection.update_many.assert_awaited_once()

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
            return_value=[{"hub_id": "hub-stale"}]
        )
        offline_cursor = MagicMock()
        offline_cursor.to_list = AsyncMock(return_value=[])

        def find_router(query, projection):
            if query.get("is_online") is True:
                return online_cursor
            return offline_cursor

        mongo.hubs_collection.find = MagicMock(side_effect=find_router)

        svc = _make_service(mongo=mongo, streams=streams)

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
            return_value=[{"hub_id": "hub-remote"}]
        )
        offline_cursor = MagicMock()
        offline_cursor.to_list = AsyncMock(return_value=[])

        def find_router(query, projection):
            if query.get("is_online") is True:
                return online_cursor
            return offline_cursor

        mongo.hubs_collection.find = MagicMock(side_effect=find_router)

        svc = _make_service(mongo=mongo, streams=streams)
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

    async def test_rejects_heartbeat_for_nonexistent_hub(self):
        """Heartbeat for a hub that doesn't exist is rejected."""
        mongo = _make_mongo()
        mongo.get_hub = AsyncMock(return_value=None)
        streams = _make_streams()
        svc = _make_service(mongo=mongo, streams=streams)
        api_key = _make_api_key()

        with pytest.raises(PermissionError, match="not owned"):
            await svc.record_hub_heartbeat("hub-nonexistent", api_key)
