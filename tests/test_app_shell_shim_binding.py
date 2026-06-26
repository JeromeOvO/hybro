from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app_shell.a2a_runtime import A2AService
from app_shell.room_runtime import AppShellRoomCenter, RoomServices
from common.dto import RoomInfo
from models.request import RoomCenterRoomSettingRequest
from room.compat.unbound import (
    UNBOUND_A2A_SERVICE,
    UNBOUND_AGENT_SELECTION_SERVICE,
    UNBOUND_AGENT_SERVICE,
    UNBOUND_DELIVERY,
    UNBOUND_TASK_SERVICE,
)


@pytest.mark.asyncio
async def test_room_services_fails_before_facade_bind() -> None:
    service = RoomServices()

    with pytest.raises(
        RuntimeError,
        match=r"RoomServices\.bind_facade\(\) not called - startup incomplete",
    ):
        await service.create_new_room(RoomCenterRoomSettingRequest(room_name="Room"))


def test_room_services_defaults_to_room_owned_unbound_legacy_dependencies() -> None:
    service = RoomServices()

    assert service.agent_service is UNBOUND_AGENT_SERVICE
    assert service.agent_selection_service is UNBOUND_AGENT_SELECTION_SERVICE
    assert service.a2a_service is UNBOUND_A2A_SERVICE
    assert not hasattr(service, "room_memory_service")
    assert service.delivery is UNBOUND_DELIVERY
    assert service.task_service is UNBOUND_TASK_SERVICE


def test_room_services_bind_legacy_dependencies_replaces_unbound_defaults() -> None:
    service = RoomServices()
    deps = {
        "agent_service": object(),
        "agent_selection_service": object(),
        "a2a_service": object(),
        "delivery": object(),
        "task_service": object(),
    }

    service.bind_legacy_dependencies(**deps)

    assert service.agent_service is deps["agent_service"]
    assert service.agent_selection_service is deps["agent_selection_service"]
    assert service.a2a_service is deps["a2a_service"]
    assert service.delivery is deps["delivery"]
    assert service.task_service is deps["task_service"]


@pytest.mark.asyncio
async def test_room_services_delegates_after_facade_bind() -> None:
    service = RoomServices()
    facade = AsyncMock()
    facade.create_room.return_value = RoomInfo(
        room_id="room-1",
        room_name="Room",
        owner_id="owner-1",
        owner_name="Owner",
    )

    service.bind_facade(facade)

    response = await service.create_new_room(
        RoomCenterRoomSettingRequest(
            room_name="Room",
            room_owner_id="owner-1",
            room_owner_name="Owner",
        )
    )

    assert response.success is True
    assert response.room_id == "room-1"
    facade.create_room.assert_awaited_once()


@pytest.mark.asyncio
async def test_room_center_fails_before_bound_room_services() -> None:
    center = AppShellRoomCenter(room_services=None)

    with pytest.raises(
        RuntimeError,
        match=r"RoomCenter\.bind_facade\(\) not called - startup incomplete",
    ):
        await center.create_new_room(MagicMock())


@pytest.mark.asyncio
async def test_room_center_delegates_after_room_services_bind() -> None:
    room_services = SimpleNamespace(
        _bound=True,
        create_new_room=AsyncMock(return_value="created"),
    )
    center = AppShellRoomCenter(room_services=room_services)

    assert await center.create_new_room(MagicMock()) == "created"
    room_services.create_new_room.assert_awaited_once()


@pytest.mark.asyncio
async def test_a2a_service_fails_before_task_store_bind() -> None:
    service = A2AService()

    with pytest.raises(
        RuntimeError,
        match=r"A2AService\.bind_task_db\(\) not called - startup incomplete",
    ):
        await service.reply_to_task("message-1", "task-1", "ctx-1", "continue")


@pytest.mark.asyncio
async def test_a2a_service_delegates_after_task_store_bind() -> None:
    service = A2AService()
    service.bind_task_db(object())
    tracking = SimpleNamespace(
        create_task_for_tracking=AsyncMock(return_value={"message_id": "message-1"})
    )
    service._task_tracking = tracking
    current_message = object()
    agent_card = object()
    message = object()

    result = await service.create_task_for_tracking(
        current_message=current_message,
        agent_card=agent_card,
        message=message,
        step_number=1,
        total_steps=2,
    )

    assert result == {"message_id": "message-1"}
    tracking.create_task_for_tracking.assert_awaited_once_with(
        current_message,
        agent_card,
        message,
        step_number=1,
        total_steps=2,
    )
