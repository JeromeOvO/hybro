from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app_shell.a2a_runtime import A2AService
from app_shell.context_assembly_service import ContextAssemblyService
from app_shell.relay_service import RelayService, init_relay_service
from app_shell.room_runtime import AppShellRoomCenter, RoomServices
from common.dto import RoomInfo
from models.request import RoomCenterRoomSettingRequest


@pytest.mark.asyncio
async def test_room_services_fails_before_facade_bind() -> None:
    service = object.__new__(RoomServices)
    service._bound = False
    service._facade = None

    with pytest.raises(
        RuntimeError,
        match=r"RoomServices\.bind_facade\(\) not called - startup incomplete",
    ):
        await service.create_new_room(RoomCenterRoomSettingRequest(room_name="Room"))


@pytest.mark.asyncio
async def test_room_services_delegates_after_facade_bind() -> None:
    service = object.__new__(RoomServices)
    service._bound = False
    service._facade = None
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


def test_context_assembly_service_fails_before_facade_bind() -> None:
    service = ContextAssemblyService()

    with pytest.raises(
        RuntimeError,
        match=(
            r"ContextAssemblyService\.bind_facade\(\) not called - startup incomplete"
        ),
    ):
        service.get_budget_summary()


def test_context_assembly_service_delegates_after_facade_bind() -> None:
    service = ContextAssemblyService()
    facade = SimpleNamespace(get_budget_summary=MagicMock(return_value={"budget": 1}))

    service.bind_facade(facade)

    assert service.get_budget_summary() == {"budget": 1}
    facade.get_budget_summary.assert_called_once_with()


def test_relay_service_fails_fast_for_missing_constructor_dependencies() -> None:
    with pytest.raises(
        ValueError,
        match="RelayService requires a mongo-compatible db/service",
    ):
        RelayService(
            mongo=None,
            legacy_store=None,
            sse_manager=object(),
            offline_failure_port=object(),
        )

    with pytest.raises(
        ValueError,
        match="init_relay_service requires offline_failure_port",
    ):
        init_relay_service(
            mongo=None,
            db=object(),
            sse_manager=object(),
            room_message_center=SimpleNamespace(agent_response_handler=object()),
        )


@pytest.mark.asyncio
async def test_relay_service_delegates_after_constructor_binding() -> None:
    service = RelayService(
        mongo=None,
        legacy_store=object(),
        sse_manager=object(),
        offline_failure_port=object(),
    )
    facade = SimpleNamespace(
        start=AsyncMock(),
        start_heartbeat_monitor=AsyncMock(),
        bind_streams=MagicMock(),
    )
    service._facade = facade

    await service.start()
    service.set_stream_service(object())

    facade.start.assert_awaited_once_with()
    facade.start_heartbeat_monitor.assert_awaited_once_with()
    facade.bind_streams.assert_called_once()
