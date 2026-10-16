"""Chat product operations: room settings, listing, membership, deletion."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chat import ChatService
from common.dto import RoomInfo
from conversation import RoomDeletionService
from models.request import RoomCenterRoomSettingRequest


@pytest.fixture
def chat_service():
    return ChatService()


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
    facade.replace_membership.return_value = facade.create_room.return_value
    facade.update_room.return_value = facade.create_room.return_value
    facade.get_room_owner.return_value = "owner"
    facade.delete_room.return_value = True

    svc.bind_facade(facade)
    memory_cleanup = SimpleNamespace(delete_room_memory=AsyncMock(return_value=True))
    svc.bind_room_deletion(
        RoomDeletionService(
            room_lifecycle=facade,
            memory_cleanup=memory_cleanup,
        )
    )

    create_response = await svc.create_new_room(
        RoomCenterRoomSettingRequest(
            room_name="Room",
            room_owner_id="owner",
            room_owner_name="Owner",
            room_agent_set={"a1": "Agent One"},
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
    replace_response = await svc.update_room_agent_set(
        RoomCenterRoomSettingRequest(
            room_id="r1",
            room_agent_set={"a1": "Agent One"},
            requesting_user_id="owner",
        )
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
    assert replace_response.success is True
    assert rename_response.success is True
    assert delete_response.success is True
    facade.create_room.assert_awaited_once()
    create_request = facade.create_room.await_args.args[0]
    assert create_request.extend_info == {"debateMode": True, "use_supervisor": True}
    facade.get_room.assert_awaited()
    facade.list_rooms_for_owner.assert_awaited_once_with("owner")
    facade.list_room_history_for_owner.assert_awaited_once_with("owner", limit=100)
    facade.replace_membership.assert_awaited_once()
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
async def test_delete_room_success_when_post_delete_context_memory_cleanup_fails():
    svc = ChatService()
    facade = AsyncMock()
    facade.get_room_owner.return_value = "owner"
    facade.delete_room.return_value = True
    svc.bind_facade(facade)
    memory_cleanup = SimpleNamespace(delete_room_memory=AsyncMock(return_value=False))
    svc.bind_room_deletion(
        RoomDeletionService(
            room_lifecycle=facade,
            memory_cleanup=memory_cleanup,
        )
    )

    response = await svc.delete_room_by_room_id(
        RoomCenterRoomSettingRequest(room_id="r1", requesting_user_id="owner")
    )

    assert response.success is True
    assert response.status_code == 200
    assert response.error is None
    facade.delete_room.assert_awaited_once_with("r1", "owner")


# =============================================================================
# _looks_like_agent_id Tests
# =============================================================================


class TestLooksLikeAgentId:
    """Tests for UUID-style agent ID detection."""

    @pytest.mark.parametrize(
        "value",
        [
            "550e8400-e29b-41d4-a716-446655440000",
            "550e8400e29b41d4a716446655440000",
        ],
    )
    def test_recognizes_valid_uuids(self, value):
        assert ChatService._looks_like_agent_id(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "MyAgent",
            "agent-name",
            "",
            "not-a-uuid-at-all",
        ],
    )
    def test_rejects_non_uuid_strings(self, value):
        assert ChatService._looks_like_agent_id(value) is False

    def test_rejects_non_string(self):
        assert ChatService._looks_like_agent_id(123) is False
        assert ChatService._looks_like_agent_id(None) is False


# =============================================================================
# _normalize_room_agent_set Tests
# =============================================================================


class TestNormalizeRoomAgentSet:
    """Tests for room_agent_set normalization."""

    def test_returns_empty_for_none(self, chat_service):
        assert chat_service._normalize_room_agent_set(None) == {}

    def test_returns_empty_for_empty(self, chat_service):
        assert chat_service._normalize_room_agent_set({}) == {}

    def test_preserves_correct_shape(self, chat_service):
        """Keys are UUIDs, values are names -- already canonical."""
        data = {"550e8400e29b41d4a716446655440000": "MyAgent"}
        result = chat_service._normalize_room_agent_set(data)
        assert result == data

    def test_flips_inverted_shape(self, chat_service):
        """Keys are names, values are UUIDs -- needs flipping."""
        data = {"MyAgent": "550e8400e29b41d4a716446655440000"}
        result = chat_service._normalize_room_agent_set(data)
        assert "550e8400e29b41d4a716446655440000" in result
        assert result["550e8400e29b41d4a716446655440000"] == "MyAgent"

    def test_handles_ambiguous_data(self, chat_service):
        """When keys and values both look like IDs, preserves original."""
        data = {"550e8400e29b41d4a716446655440000": "660e8400e29b41d4a716446655440000"}
        result = chat_service._normalize_room_agent_set(data)
        assert result == data
