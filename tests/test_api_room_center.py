"""
Unit tests for Room Center API endpoints.

Tests cover:
- Room creation
- Room settings inquiry
- Room ownership verification
- Room updates (agent set, name, extend info)
- Message creation and retrieval
- Authorization checks
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.room_center import (
    create_new_room,
    inquiry_active_runs,
    inquiry_room_messages,
    inquiry_room_setting,
    inquiry_rooms_by_room_owner_id,
    send_message,
    suggest_agents,
    update_room_agent_set,
    update_room_extend_info,
    update_room_name,
    verify_room_ownership,
)
from app_shell.room_runtime import AppShellRoomCenter as RoomCenter
from common.dto import ExecutionAck, RunInfo
from models.response import (
    RoomCenterRoomMessageResponse,
    RoomCenterRoomSettingResponse,
)
from tests.conftest import PATCH

# =============================================================================
# Room Ownership Verification Tests
# =============================================================================


class TestRoomCenterAdapter:
    @pytest.mark.asyncio
    async def test_fails_before_bind_when_service_unbound(self):
        center = RoomCenter(room_services=None)

        with pytest.raises(
            RuntimeError,
            match=r"RoomCenter\.bind_facade\(\) not called - startup incomplete",
        ):
            await center.create_new_room(MagicMock())

    @pytest.mark.asyncio
    async def test_delegates_to_bound_room_services(self):
        service = MagicMock()
        service._bound = True
        service.create_new_room = AsyncMock(return_value="created")
        center = RoomCenter(room_services=service)

        assert await center.create_new_room(MagicMock()) == "created"
        service.create_new_room.assert_awaited_once()


class TestVerifyRoomOwnership:
    """Tests for verify_room_ownership helper function."""

    @pytest.mark.asyncio
    async def test_raises_400_when_room_id_empty(self, mock_user):
        """Should raise 400 when room_id is empty."""
        with pytest.raises(HTTPException) as exc_info:
            await verify_room_ownership("", mock_user)
        
        assert exc_info.value.status_code == 400
        assert "room_id is required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_404_when_room_not_found(self, mock_user, mock_db_service):
        """Should raise 404 when room doesn't exist."""
        mock_db_service.get_room_by_room_id.return_value = None
        
        with patch(PATCH["room_center.room_store"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await verify_room_ownership("nonexistent-room", mock_user)
        
        assert exc_info.value.status_code == 404
        assert "Room not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_403_when_user_not_owner(
        self, mock_user, mock_user_2, mock_db_service, sample_room
    ):
        """Should raise 403 when user is not the room owner."""
        # Room owned by mock_user, but mock_user_2 is trying to access
        mock_db_service.get_room_by_room_id.return_value = sample_room
        
        with patch(PATCH["room_center.room_store"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await verify_room_ownership(sample_room.room_id, mock_user_2)
        
        assert exc_info.value.status_code == 403
        assert "permission" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_passes_when_user_is_owner(
        self, mock_user, mock_db_service, sample_room
    ):
        """Should pass without exception when user is the owner."""
        mock_db_service.get_room_by_room_id.return_value = sample_room
        
        with patch(PATCH["room_center.room_store"], mock_db_service):
            # Should not raise
            await verify_room_ownership(sample_room.room_id, mock_user)


# =============================================================================
# Room Creation Tests
# =============================================================================


class TestCreateNewRoom:
    """Tests for create_new_room endpoint."""

    @pytest.mark.asyncio
    async def test_creates_room_with_user_as_owner(
        self, mock_user, mock_room_center
    ):
        """Should create room with authenticated user as owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_name": "Test Room",
            "room_owner_name": "Test User",
            "room_agent_set": {"agent-1": "Agent One"},
            "extend_info": {"debateMode": True, "use_supervisor": True},
        })
        
        expected_response = RoomCenterRoomSettingResponse(
            success=True,
            room_id="new-room-id",
            status_code=200,
        )
        mock_room_center.create_new_room.return_value = expected_response
        
        with patch(PATCH["room_center.room_center"], mock_room_center):
            response = await create_new_room(mock_request, mock_user)
        
        assert response.success is True
        assert response.room_id == "new-room-id"
        
        # Verify the request was made with user's ID as owner
        call_args = mock_room_center.create_new_room.call_args[0][0]
        assert call_args.room_owner_id == mock_user.user_id
        assert call_args.extend_info == {"debateMode": True, "use_supervisor": True}

    @pytest.mark.asyncio
    async def test_creates_room_with_agent_group(
        self, mock_user, mock_room_center
    ):
        """Should create room with applied_from_group when specified."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_name": "Group Room",
            "room_owner_name": "Test User",
            "room_agent_set": {},
            "applied_from_group": "group-123",
        })
        
        expected_response = RoomCenterRoomSettingResponse(success=True, room_id="new-room-id")
        mock_room_center.create_new_room.return_value = expected_response
        
        with patch(PATCH["room_center.room_center"], mock_room_center):
            await create_new_room(mock_request, mock_user)
        
        call_args = mock_room_center.create_new_room.call_args[0][0]
        assert call_args.applied_from_group == "group-123"


# =============================================================================
# Room Settings Inquiry Tests
# =============================================================================


class TestInquiryRoomSetting:
    """Tests for inquiry_room_setting endpoint."""

    @pytest.mark.asyncio
    async def test_returns_room_settings_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        """Should return room settings when user is owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})
        
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room
        expected_response = RoomCenterRoomSettingResponse(
            success=True,
            room=sample_room,
        )
        patch_room_center_deps["room_center"].inquiry_room_setting.return_value = expected_response
        
        response = await inquiry_room_setting(mock_request, mock_user)
        
        assert response.success is True
        assert response.room == sample_room

    @pytest.mark.asyncio
    async def test_raises_403_for_non_owner(
        self, mock_user_2, mock_db_service, sample_room
    ):
        """Should raise 403 when user is not the owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})
        
        mock_db_service.get_room_by_room_id.return_value = sample_room
        
        with patch(PATCH["room_center.room_store"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await inquiry_room_setting(mock_request, mock_user_2)
        
        assert exc_info.value.status_code == 403


# =============================================================================
# Active runs inquiry (lightweight reconcile)
# =============================================================================


class TestInquiryActiveRuns:
    """Tests for inquiry_active_runs endpoint."""

    @pytest.mark.asyncio
    async def test_returns_active_runs_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})

        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = (
            sample_room
        )
        run = RunInfo(
            run_id="run-1",
            room_id=sample_room.room_id,
            state="processing",
            trigger_message_id="m1",
            agent_id="a1",
            seq=1,
        )
        patch_room_center_deps["execution_engine"].get_runs_for_room.return_value = [run]

        response = await inquiry_active_runs(mock_request, mock_user)

        assert response.success is True
        assert response.room_id == sample_room.room_id
        assert response.active_runs is not None
        assert len(response.active_runs) == 1
        assert response.active_runs[0].run_id == "run-1"
        patch_room_center_deps["execution_engine"].get_runs_for_room.assert_awaited_once_with(
            sample_room.room_id
        )

    @pytest.mark.asyncio
    async def test_raises_403_for_non_owner(
        self, mock_user_2, mock_db_service, sample_room
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"room_id": sample_room.room_id})

        mock_db_service.get_room_by_room_id.return_value = sample_room

        with patch(PATCH["room_center.room_store"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await inquiry_active_runs(mock_request, mock_user_2)

        assert exc_info.value.status_code == 403


# =============================================================================
# Room List by Owner Tests
# =============================================================================


class TestInquiryRoomsByRoomOwnerId:
    """Tests for inquiry_rooms_by_room_owner_id endpoint."""

    @pytest.mark.asyncio
    async def test_returns_rooms_for_own_user_id(
        self, mock_user, mock_room_center, sample_room
    ):
        """Should return rooms when requesting own rooms."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_owner_id": mock_user.user_id
        })
        
        expected_response = RoomCenterRoomSettingResponse(
            success=True,
            room_list=[sample_room],
        )
        mock_room_center.inquiry_rooms_by_room_owner_id.return_value = expected_response
        
        with patch(PATCH["room_center.room_center"], mock_room_center):
            response = await inquiry_rooms_by_room_owner_id(mock_request, mock_user)
        
        assert response.success is True
        assert len(response.room_list) == 1

    @pytest.mark.asyncio
    async def test_raises_403_for_other_user_rooms(self, mock_user):
        """Should raise 403 when requesting another user's rooms."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_owner_id": "other-user-id"
        })
        
        with pytest.raises(HTTPException) as exc_info:
            await inquiry_rooms_by_room_owner_id(mock_request, mock_user)
        
        assert exc_info.value.status_code == 403


# =============================================================================
# Room Update Tests
# =============================================================================


class TestUpdateRoomAgentSet:
    """Tests for update_room_agent_set endpoint."""

    @pytest.mark.asyncio
    async def test_updates_agent_set_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        """Should update agent set when user is owner."""
        new_agent_set = {"agent-2": "Agent Two", "agent-3": "Agent Three"}
        
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "room_agent_set": new_agent_set,
        })
        
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room
        expected_response = RoomCenterRoomSettingResponse(success=True)
        patch_room_center_deps["room_center"].update_room_agent_set.return_value = expected_response
        
        response = await update_room_agent_set(mock_request, mock_user)
        
        assert response.success is True
        
        # Verify requesting_user_id is passed for visibility validation
        call_args = patch_room_center_deps["room_center"].update_room_agent_set.call_args[0][0]
        assert call_args.requesting_user_id == mock_user.user_id


class TestUpdateRoomName:
    """Tests for update_room_name endpoint."""

    @pytest.mark.asyncio
    async def test_updates_room_name_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        """Should update room name when user is owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "room_name": "New Room Name",
        })
        
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room
        expected_response = RoomCenterRoomSettingResponse(success=True)
        patch_room_center_deps["room_center"].update_room_name.return_value = expected_response
        
        response = await update_room_name(mock_request, mock_user)
        
        assert response.success is True
        call_args = patch_room_center_deps["room_center"].update_room_name.call_args[0][0]
        assert call_args.room_name == "New Room Name"


class TestUpdateRoomExtendInfo:
    """Tests for update_room_extend_info endpoint."""

    @pytest.mark.asyncio
    async def test_updates_extend_info_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        """Should update extend info when user is owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "extend_info": {"custom_field": "custom_value"},
        })
        
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room
        expected_response = RoomCenterRoomSettingResponse(success=True)
        patch_room_center_deps["room_center"].update_room_extend_info.return_value = expected_response
        
        response = await update_room_extend_info(mock_request, mock_user)
        
        assert response.success is True
        call_args = patch_room_center_deps["room_center"].update_room_extend_info.call_args[0][0]
        assert call_args.extend_info == {"custom_field": "custom_value"}


class TestUpdateEndpointsRejectNonOwner:
    """Non-owner is rejected for all update endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint_fn,payload", [
        (update_room_agent_set, {"room_id": "test-room-001", "room_agent_set": {}}),
        (update_room_name, {"room_id": "test-room-001", "room_name": "X"}),
        (update_room_extend_info, {"room_id": "test-room-001", "extend_info": {}}),
    ])
    async def test_rejects_non_owner(
        self, mock_user_2, mock_db_service, sample_room, endpoint_fn, payload
    ):
        """All update endpoints should raise 403 for non-owners."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value=payload)
        mock_db_service.get_room_by_room_id.return_value = sample_room

        with patch(PATCH["room_center.room_store"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await endpoint_fn(mock_request, mock_user_2)

        assert exc_info.value.status_code == 403


# =============================================================================
# Message Tests
# =============================================================================


class TestInquiryRoomMessages:
    """Tests for inquiry_room_messages endpoint."""

    @pytest.mark.asyncio
    async def test_returns_messages_for_owner(
        self, mock_user, sample_room, patch_room_center_deps
    ):
        """Should return messages when user is owner."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
        })
        
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room
        expected_response = RoomCenterRoomMessageResponse(
            success=True,
            message_list=[],
        )
        patch_room_center_deps["room_center"].inquiry_room_messages_by_room_id.return_value = expected_response
        
        response = await inquiry_room_messages(mock_request, mock_user)
        
        assert response.success is True


class TestSendMessage:
    """Tests for send_message endpoint."""

    @pytest.mark.asyncio
    async def test_sends_message_and_triggers_processing(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        """Should send message and trigger background processing."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "message_target_mode": "room_default",
            "client_request_id": "c7c9a000-0000-4000-8000-000000000001",
        })
        
        mock_background_tasks = MagicMock()
        
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room
        expected_response = ExecutionAck(
            success=True,
            message_id="new-message-id",
        )
        patch_room_center_deps["execution_engine"].execute.return_value = expected_response
        
        response = await send_message(
            mock_request, mock_background_tasks, mock_user
        )
        
        assert response.success is True
        assert response.message_id == "new-message-id"
        
        # Verify background task was added
        mock_background_tasks.add_task.assert_called_once()
        execution_request = patch_room_center_deps[
            "execution_engine"
        ].execute.await_args.args[0]
        assert execution_request.room_id == sample_room.room_id
        assert execution_request.sender_id == mock_user.user_id
        assert execution_request.target_group == "room_team"
        assert execution_request.message_target_mode == "room_default"
        assert execution_request.mentioned_agent_ids is None

    @pytest.mark.asyncio
    async def test_send_message_rejects_missing_message_target_mode_without_mentions(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000099",
        })
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

        response = await send_message(mock_request, MagicMock(), mock_user)

        assert response.status_code == 400
        assert "message_target_mode is required" in response.error

    @pytest.mark.asyncio
    async def test_send_message_rejects_legacy_target_group(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000098",
            "target_group": "room_team",
        })
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

        response = await send_message(mock_request, MagicMock(), mock_user)

        assert response.status_code == 400
        assert "target_group is no longer supported" in response.error

    @pytest.mark.asyncio
    async def test_send_message_rejects_unknown_message_target_mode(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000097",
            "message_target_mode": "room_team",
        })
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

        response = await send_message(mock_request, MagicMock(), mock_user)

        assert response.status_code == 400
        assert "message_target_mode must be one of" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_message_saved_group_requires_target_group_id(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000096",
            "message_target_mode": "saved_group",
        })
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

        response = await send_message(mock_request, MagicMock(), mock_user)

        assert response.status_code == 400
        assert "target_group_id is required" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_message_saved_group_rejects_malformed_target_group_id(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000091",
            "message_target_mode": "saved_group",
            "target_group_id": {"id": "group-123"},
        })
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

        response = await send_message(mock_request, MagicMock(), mock_user)

        assert response.status_code == 400
        assert "target_group_id is required" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reserved_id", ["room_team", "all_agents"])
    async def test_send_message_saved_group_rejects_reserved_target_group_id(
        self,
        reserved_id,
        mock_user,
        sample_room,
        sample_user_message,
        patch_room_center_deps,
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000090",
            "message_target_mode": "saved_group",
            "target_group_id": reserved_id,
        })
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

        response = await send_message(mock_request, MagicMock(), mock_user)

        assert response.status_code == 400
        assert "target_group_id cannot be a reserved target group" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_message_saved_group_uses_target_group_id(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000095",
            "message_target_mode": "saved_group",
            "target_group_id": " group-123 ",
        })
        mock_background_tasks = MagicMock()
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps["execution_engine"].execute.return_value = ExecutionAck(
            success=True,
            message_id="new-message-id",
        )

        response = await send_message(mock_request, mock_background_tasks, mock_user)

        assert response.success is True
        execution_request = patch_room_center_deps[
            "execution_engine"
        ].execute.await_args.args[0]
        assert execution_request.target_group == "group-123"
        assert execution_request.target_group_id == "group-123"
        assert execution_request.message_target_mode == "saved_group"

    @pytest.mark.asyncio
    async def test_send_message_all_agents_uses_explicit_mode(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000094",
            "message_target_mode": "all_agents",
        })
        mock_background_tasks = MagicMock()
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps["execution_engine"].execute.return_value = ExecutionAck(
            success=True,
            message_id="new-message-id",
        )

        response = await send_message(mock_request, mock_background_tasks, mock_user)

        assert response.success is True
        execution_request = patch_room_center_deps[
            "execution_engine"
        ].execute.await_args.args[0]
        assert execution_request.target_group == "all_agents"
        assert execution_request.message_target_mode == "all_agents"

    @pytest.mark.asyncio
    async def test_send_message_rejects_target_group_id_for_non_saved_group(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000093",
            "message_target_mode": "room_default",
            "target_group_id": "group-123",
        })
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

        response = await send_message(mock_request, MagicMock(), mock_user)

        assert response.status_code == 400
        assert "target_group_id is only supported" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target_group_id", [0, []])
    async def test_send_message_rejects_falsy_target_group_id_for_non_saved_group(
        self,
        target_group_id,
        mock_user,
        sample_room,
        sample_user_message,
        patch_room_center_deps,
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000089",
            "message_target_mode": "room_default",
            "target_group_id": target_group_id,
        })
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

        response = await send_message(mock_request, MagicMock(), mock_user)

        assert response.status_code == 400
        assert "target_group_id is only supported" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_message_rejects_target_group_id_with_mentions(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000092",
            "mentioned_agent_ids": ["agent-1"],
            "target_group_id": "group-123",
        })
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

        response = await send_message(mock_request, MagicMock(), mock_user)

        assert response.status_code == 400
        assert "target_group_id is only supported" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_message_rejects_falsy_target_group_id_with_mentions(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000088",
            "mentioned_agent_ids": ["agent-1"],
            "target_group_id": 0,
        })
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

        response = await send_message(mock_request, MagicMock(), mock_user)

        assert response.status_code == 400
        assert "target_group_id is only supported" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "mentioned_agent_ids",
        ["agent-1", [1], [" "], {"agent_id": "agent-1"}],
    )
    async def test_send_message_rejects_malformed_mentioned_agent_ids(
        self,
        mentioned_agent_ids,
        mock_user,
        sample_room,
        sample_user_message,
        patch_room_center_deps,
    ):
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "client_request_id": "c7c9a000-0000-4000-8000-000000000087",
            "mentioned_agent_ids": mentioned_agent_ids,
        })
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

        response = await send_message(mock_request, MagicMock(), mock_user)

        assert response.status_code == 400
        assert "mentioned_agent_ids must be a list of non-empty strings" in response.error
        patch_room_center_deps["execution_engine"].execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_trigger_processing_on_failure(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        """Should not trigger processing when message creation fails."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "message_target_mode": "room_default",
            "client_request_id": "c7c9a000-0000-4000-8000-000000000002",
        })
        
        mock_background_tasks = MagicMock()
        
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room
        expected_response = ExecutionAck(
            success=False,
            error="Failed to create message",
        )
        patch_room_center_deps["execution_engine"].execute.return_value = expected_response
        
        response = await send_message(
            mock_request, mock_background_tasks, mock_user
        )
        
        assert response.success is False
        mock_background_tasks.add_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_trigger_processing_when_ack_says_skip(
        self, mock_user, sample_room, sample_user_message, patch_room_center_deps
    ):
        """Should not start orchestration when execution already emitted terminal status."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(),
            "message_target_mode": "room_default",
            "client_request_id": "c7c9a000-0000-4000-8000-000000000012",
        })
        mock_background_tasks = MagicMock()
        patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room
        patch_room_center_deps["execution_engine"].execute.return_value = ExecutionAck(
            success=True,
            message_id="new-message-id",
            should_start_orchestration=False,
        )

        response = await send_message(mock_request, mock_background_tasks, mock_user)

        assert response.success is True
        assert response.message_id == "new-message-id"
        mock_background_tasks.add_task.assert_not_called()


# =============================================================================
# Agent Suggestion Tests
# =============================================================================


class TestSuggestAgents:
    """Tests for suggest_agents endpoint."""

    @pytest.mark.asyncio
    async def test_returns_suggestions_for_valid_message(self):
        """Should return agent suggestions for valid message."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "message_text": "Help me write some code",
            "top_k": 3,
        })
        
        mock_selection_service = MagicMock()
        mock_selection_service.suggest_agents = AsyncMock(return_value={
            "agents": [
                {"agent_id": "agent-1", "score": 0.9},
                {"agent_id": "agent-2", "score": 0.8},
            ]
        })
        
        response = await suggest_agents(
            mock_request,
            selection_service=mock_selection_service,
        )
        
        assert response["success"] is True
        assert "agents" in response

    @pytest.mark.asyncio
    async def test_returns_error_for_empty_message(self):
        """Should return error when message_text is empty."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "message_text": "",
            "top_k": 3,
        })
        
        response = await suggest_agents(mock_request)
        
        assert response["success"] is False
        assert response["status_code"] == 400

    @pytest.mark.asyncio
    async def test_handles_service_error(self):
        """Should handle errors from agent selection service."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "message_text": "Test message",
            "top_k": 3,
        })
        
        mock_selection_service = MagicMock()
        mock_selection_service.suggest_agents = AsyncMock(
            side_effect=Exception("Service error")
        )
        
        with patch(
            PATCH["agent_selection_service"],
            mock_selection_service
        ):
            response = await suggest_agents(mock_request)
        
        assert response["success"] is False
        assert response["status_code"] == 500
