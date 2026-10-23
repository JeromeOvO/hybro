"""Chat product operations: room settings, listing, membership, deletion."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chat import ChatService
from common.dto import RoomInfo
from conversation import RoomDeletionService
from models.request import RoomCenterRoomSettingRequest


def _file_lifecycle():
    return SimpleNamespace(
        begin_room_deletion=AsyncMock(return_value="deletion-1"),
        wait_for_room_writes=AsyncMock(return_value=True),
        set_deletion_phase=AsyncMock(return_value=True),
        delete_for_room=AsyncMock(return_value=0),
        delete_room_state=AsyncMock(return_value=True),
    )


@pytest.mark.asyncio
async def test_chat_service_delegated_methods_fail_before_bind():
    svc = ChatService()

    with pytest.raises(
        RuntimeError,
        match=r"ChatService\.bind_facade\(\) not called - startup incomplete",
    ):
        await svc.create_new_room(RoomCenterRoomSettingRequest(room_name="Room"))


@pytest.mark.asyncio
async def test_chat_service_bind_facade_delegates_room_lifecycle_methods():
    svc = ChatService()
    facade = AsyncMock()
    facade.create_room.return_value = RoomInfo(
        room_id="r1",
        room_name="Room",
        owner_id="owner",
        owner_name="Owner",
        agent_ids=["a1"],
        agent_set={"a1": "Agent One"},
    )
    facade.get_room.return_value = facade.create_room.return_value
    facade.list_rooms_for_owner.return_value = [facade.create_room.return_value]
    facade.list_room_history_for_owner.return_value = [facade.create_room.return_value]
    facade.update_room.return_value = facade.create_room.return_value
    facade.get_room_owner.return_value = "owner"
    facade.delete_room.return_value = True

    svc.bind_facade(facade)
    memory_cleanup = SimpleNamespace(delete_room_memory=AsyncMock(return_value=True))
    svc.bind_room_deletion(
        RoomDeletionService(
            room_lifecycle=facade,
            file_lifecycle=_file_lifecycle(),
            memory_cleanup=memory_cleanup,
        )
    )

    create_response = await svc.create_new_room(
        RoomCenterRoomSettingRequest(
            room_name="Room",
            room_owner_id="owner",
            room_owner_name="Owner",
            membership_seed_input="manual",
            room_agent_ids=["a1"],
            extend_info={"debateMode": True, "use_supervisor": True},
            requesting_user_id="owner",
        )
    )
    inquiry_response = await svc.inquiry_room_setting(
        RoomCenterRoomSettingRequest(room_id="r1", requesting_user_id="owner")
    )
    list_response = await svc.inquiry_rooms_by_room_owner_id(
        RoomCenterRoomSettingRequest(room_owner_id="owner")
    )
    history_response = await svc.inquiry_room_history_by_owner_id(
        RoomCenterRoomSettingRequest(room_owner_id="owner")
    )
    rename_response = await svc.update_room_name(
        RoomCenterRoomSettingRequest(room_id="r1", room_name="Renamed")
    )
    delete_response = await svc.delete_room_by_room_id(
        RoomCenterRoomSettingRequest(room_id="r1", requesting_user_id="owner")
    )

    assert create_response.success is True
    assert create_response.room.room_id == "r1"
    assert inquiry_response.room.room_agent_set == {"a1": "Agent One"}
    assert list_response.room_list[0].room_id == "r1"
    assert history_response.room_list[0].room_id == "r1"
    assert rename_response.success is True
    assert delete_response.success is True
    facade.create_room.assert_awaited_once()
    create_request = facade.create_room.await_args.args[0]
    assert create_request.extend_info == {"debateMode": True, "use_supervisor": True}
    assert create_request.membership_seed.mode == "manual"
    assert create_request.membership_seed.agent_ids == ["a1"]
    facade.get_room.assert_awaited()
    facade.list_rooms_for_owner.assert_awaited_once_with("owner")
    facade.list_room_history_for_owner.assert_awaited_once_with("owner", limit=100)
    facade.update_room.assert_awaited_once_with("r1", {"room_name": "Renamed"})
    facade.delete_room.assert_awaited_once_with("r1", "owner")


@pytest.mark.asyncio
async def test_chat_service_room_setting_returns_room_metadata_only():
    svc = ChatService()
    facade = AsyncMock()
    facade.get_room.return_value = RoomInfo(
        room_id="r1",
        room_name="Room",
        owner_id="owner",
        owner_name="Owner",
    )
    svc.bind_facade(facade)

    response = await svc.inquiry_room_setting(
        RoomCenterRoomSettingRequest(room_id="r1")
    )

    assert response.success is True
    assert response.active_runs is None
    facade.get_room.assert_awaited_once_with("r1")


@pytest.mark.asyncio
async def test_delete_room_does_not_cleanup_when_requester_is_not_owner():
    svc = ChatService()
    facade = AsyncMock()
    facade.get_room_owner.return_value = "owner"
    facade.delete_room.return_value = True
    svc.bind_facade(facade)
    memory_cleanup = SimpleNamespace(delete_room_memory=AsyncMock(return_value=True))
    svc.bind_room_deletion(
        RoomDeletionService(
            room_lifecycle=facade,
            file_lifecycle=_file_lifecycle(),
            memory_cleanup=memory_cleanup,
        )
    )

    response = await svc.delete_room_by_room_id(
        RoomCenterRoomSettingRequest(room_id="r1", requesting_user_id="intruder")
    )

    assert response.success is False
    assert response.status_code == 403
    assert response.error == "Forbidden"
    facade.delete_room.assert_not_awaited()
    memory_cleanup.delete_room_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_room_without_membership_input_seeds_empty_manual_membership():
    svc = ChatService()
    facade = AsyncMock()
    facade.create_room.return_value = RoomInfo(
        room_id="r1", room_name="Room", owner_id="owner", owner_name="Owner"
    )
    svc.bind_facade(facade)

    response = await svc.create_new_room(
        RoomCenterRoomSettingRequest(
            room_name="Room",
            room_owner_id="owner",
            room_owner_name="Owner",
            requesting_user_id="owner",
        )
    )

    assert response.success is True
    seed = facade.create_room.await_args.args[0].membership_seed
    assert seed.mode == "manual"
    assert seed.agent_ids is None
