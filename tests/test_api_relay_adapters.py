from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.hub import hub_status_for_user
from api.relay import RegisterHubRequest, relay_register, relay_status
from common.auth import ClerkUser
from hub_runtime_bridge.adapters.relay_hub_store import RelayHubStore
from models.api_key import APIKey


def _api_key() -> APIKey:
    return APIKey(
        key_id="key-1",
        key_hash="hash",
        user_id="user-1",
        name="Test key",
    )


def _user() -> ClerkUser:
    return ClerkUser(user_id="user-1", session_id="session-1", claims={})


@pytest.mark.asyncio
async def test_relay_register_uses_injected_service():
    svc = SimpleNamespace(
        register_hub=AsyncMock(
            return_value=SimpleNamespace(hub_id="hub-1", user_id="user-1")
        )
    )

    response = await relay_register(
        RegisterHubRequest(hub_id="hub-1"),
        _api_key(),
        svc=svc,
    )

    assert response.hub_id == "hub-1"
    assert response.user_id == "user-1"
    svc.register_hub.assert_awaited_once()


@pytest.mark.asyncio
async def test_relay_status_uses_injected_service():
    svc = SimpleNamespace(get_hub_status=AsyncMock(return_value=[]))

    response = await relay_status(_api_key(), svc=svc)

    assert response.hubs == []
    svc.get_hub_status.assert_awaited_once_with("user-1")


@pytest.mark.asyncio
async def test_hub_status_for_user_uses_shared_injected_relay_service():
    svc = SimpleNamespace(get_hub_status=AsyncMock(return_value=[]))

    response = await hub_status_for_user(_user(), svc=svc)

    assert response.hubs == []
    svc.get_hub_status.assert_awaited_once_with("user-1")


@pytest.mark.asyncio
async def test_relay_hub_store_delegates_hub_and_agent_repository_calls():
    mongo = SimpleNamespace(collection=lambda name: f"collection:{name}")
    hub_repository = SimpleNamespace(
        upsert=AsyncMock(),
        get_by_id=AsyncMock(return_value={"hub_id": "hub-1"}),
        get_by_owner=AsyncMock(return_value=[{"hub_id": "hub-1"}]),
        update_hub_status=AsyncMock(),
        update_hub_status_if_current=AsyncMock(return_value=True),
    )
    agent_repository = SimpleNamespace(
        count_hub_agents=AsyncMock(return_value=(2, 1)),
        increment_agent_call_count=AsyncMock(),
    )
    store = RelayHubStore(
        mongo=mongo,
        hub_repository=hub_repository,
        agent_repository=agent_repository,
    )

    await store.upsert_hub({"hub_id": "hub-1", "user_id": "user-1"})
    assert await store.get_hub("hub-1") == {"hub_id": "hub-1"}
    assert await store.get_hubs_by_user("user-1") == [{"hub_id": "hub-1"}]
    await store.update_hub_status("hub-1", is_online=True)
    assert await store.update_hub_status_if_current(
        "hub-1",
        connection_id="conn-1",
        is_online=False,
    )
    assert await store.count_hub_agents("hub-1") == (2, 1)
    await store.increment_agent_call_count("agent-1", success=True)

    hub_repository.upsert.assert_awaited_once_with(
        "hub-1",
        {"hub_id": "hub-1", "user_id": "user-1"},
    )
    hub_repository.update_hub_status.assert_awaited_once_with(
        "hub-1",
        is_online=True,
    )
    hub_repository.update_hub_status_if_current.assert_awaited_once_with(
        "hub-1",
        connection_id="conn-1",
        is_online=False,
    )
    agent_repository.count_hub_agents.assert_awaited_once_with("hub-1")
    agent_repository.increment_agent_call_count.assert_awaited_once_with(
        "agent-1",
        success=True,
    )
    assert store.collection("hubs") == "collection:hubs"


@pytest.mark.asyncio
async def test_relay_hub_store_requires_hub_id_for_upsert():
    store = RelayHubStore(
        mongo=SimpleNamespace(collection=lambda name: None),
        hub_repository=SimpleNamespace(upsert=AsyncMock()),
        agent_repository=SimpleNamespace(),
    )

    with pytest.raises(ValueError, match="hub_id"):
        await store.upsert_hub({})
