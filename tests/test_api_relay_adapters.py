from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.hub import hub_status_for_user
from api.relay import RegisterHubRequest, relay_register, relay_status
from common.auth import ClerkUser
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
