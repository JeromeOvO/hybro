"""
Unit tests for SSE (Server-Sent Events) API endpoints.

Tests cover:
- SSE stream connection
- Room status retrieval
- Message cancellation
- Authorization checks
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from api.sse import (
    cancel_message,
    get_room_sse_status,
    stream_room_messages,
)
from tests.conftest import PATCH

# =============================================================================
# SSE Stream Tests
# =============================================================================


class TestStreamRoomMessages:
    """Tests for stream_room_messages endpoint."""

    @pytest.mark.asyncio
    async def test_returns_streaming_response(
        self, mock_user, mock_sse_manager, sample_room
    ):
        """Should return a StreamingResponse for SSE."""
        mock_connection = MagicMock()
        mock_connection.connection_id = "conn-123"
        mock_connection.is_active = False
        mock_connection.get_message = AsyncMock(return_value=None)

        mock_sse_manager.add_connection.return_value = mock_connection

        with patch(PATCH["sse.sse_manager"], mock_sse_manager):
            response = await stream_room_messages(sample_room.room_id, mock_user)

        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"

    @pytest.mark.asyncio
    async def test_sets_correct_headers(
        self, mock_user, mock_sse_manager, sample_room
    ):
        """Should set correct SSE headers."""
        mock_connection = MagicMock()
        mock_connection.connection_id = "conn-123"
        mock_connection.is_active = False
        mock_connection.get_message = AsyncMock(return_value=None)

        mock_sse_manager.add_connection.return_value = mock_connection

        with patch(PATCH["sse.sse_manager"], mock_sse_manager):
            response = await stream_room_messages(sample_room.room_id, mock_user)

        assert response.headers["Cache-Control"] == "no-cache"
        assert response.headers["Connection"] == "keep-alive"

    @pytest.mark.asyncio
    async def test_raises_403_when_stream_user_does_not_own_room(
        self, mock_user_2, sample_room, patch_sse_deps
    ):
        """Should not open an SSE stream for a room owned by another user."""
        deps = patch_sse_deps
        deps["db_service"].get_room_by_room_id.return_value = sample_room

        with pytest.raises(HTTPException) as exc_info:
            await stream_room_messages(
                room_id=sample_room.room_id,
                user=mock_user_2,
                manager=deps["sse_manager"],
                db=deps["db_service"],
            )

        assert exc_info.value.status_code == 403
        deps["sse_manager"].add_connection.assert_not_called()


# =============================================================================
# Room SSE Status Tests
# =============================================================================


class TestGetRoomSseStatus:
    """Tests for get_room_sse_status endpoint."""

    @pytest.mark.asyncio
    async def test_returns_room_status(
        self, mock_user, mock_sse_manager, sample_room
    ):
        """Should return SSE connection status for room."""
        mock_sse_manager.get_room_status.return_value = {
            "room_id": sample_room.room_id,
            "connections": 2,
            "active": True,
        }

        with patch(PATCH["sse.sse_manager"], mock_sse_manager):
            result = await get_room_sse_status(sample_room.room_id, mock_user)

        assert result["connections"] == 2
        mock_sse_manager.get_room_status.assert_called_once_with(sample_room.room_id)

    @pytest.mark.asyncio
    async def test_raises_403_when_status_user_does_not_own_room(
        self, mock_user_2, sample_room, patch_sse_deps
    ):
        """Should not disclose SSE status for a room owned by another user."""
        deps = patch_sse_deps
        deps["db_service"].get_room_by_room_id.return_value = sample_room

        with pytest.raises(HTTPException) as exc_info:
            await get_room_sse_status(
                room_id=sample_room.room_id,
                user=mock_user_2,
                manager=deps["sse_manager"],
                db=deps["db_service"],
            )

        assert exc_info.value.status_code == 403
        deps["sse_manager"].get_room_status.assert_not_called()


# =============================================================================
# Message Cancellation Tests
# =============================================================================


class TestCancelMessage:
    """Tests for cancel_message endpoint."""

    @pytest.mark.asyncio
    async def test_cancels_message_for_owner(
        self, mock_user, sample_room, sample_user_message, patch_sse_deps
    ):
        """Should cancel message when user owns the room."""
        deps = patch_sse_deps
        deps["db_service"].get_room_user_message_by_message_id.return_value = sample_user_message
        deps["db_service"].get_room_by_room_id.return_value = sample_room

        result = await cancel_message(sample_user_message.message_id, mock_user)

        assert result["success"] is True
        assert result["message_id"] == sample_user_message.message_id
        deps["execution_engine"].cancel.assert_awaited_once_with(
            room_id=sample_user_message.room_id,
            message_id=sample_user_message.message_id,
            requested_by_user_id=mock_user.user_id,
        )

    @pytest.mark.asyncio
    async def test_raises_404_when_message_not_found(
        self, mock_user, mock_db_service
    ):
        """Should raise 404 when message doesn't exist."""
        mock_db_service.get_room_user_message_by_message_id.return_value = None

        with patch(PATCH["sse.db_service"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await cancel_message("nonexistent-message", mock_user)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_room_not_found(
        self, mock_user, mock_db_service, sample_user_message
    ):
        """Should raise 404 when room doesn't exist."""
        mock_db_service.get_room_user_message_by_message_id.return_value = sample_user_message
        mock_db_service.get_room_by_room_id.return_value = None

        with patch(PATCH["sse.db_service"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await cancel_message(sample_user_message.message_id, mock_user)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_403_when_not_room_owner(
        self, mock_user_2, mock_db_service, sample_room, sample_user_message
    ):
        """Should raise 403 when user doesn't own the room."""
        mock_db_service.get_room_user_message_by_message_id.return_value = sample_user_message
        mock_db_service.get_room_by_room_id.return_value = sample_room

        with patch(PATCH["sse.db_service"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await cancel_message(sample_user_message.message_id, mock_user_2)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_500_when_execution_cancel_returns_false(
        self, mock_user, sample_room, sample_user_message, patch_sse_deps
    ):
        """Should return 500 when execution cancellation persistence fails."""
        deps = patch_sse_deps
        deps["db_service"].get_room_user_message_by_message_id.return_value = sample_user_message
        deps["db_service"].get_room_by_room_id.return_value = sample_room
        deps["execution_engine"].cancel.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await cancel_message(sample_user_message.message_id, mock_user)

        assert exc_info.value.status_code == 500
        assert "Failed to persist cancellation" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_execution_cancel_receives_audit_user(
        self, mock_user, sample_room, sample_user_message, patch_sse_deps
    ):
        """Should pass room, message, and requesting user to Execution."""
        deps = patch_sse_deps
        deps["db_service"].get_room_user_message_by_message_id.return_value = sample_user_message
        deps["db_service"].get_room_by_room_id.return_value = sample_room

        await cancel_message(sample_user_message.message_id, mock_user)

        deps["execution_engine"].cancel.assert_awaited_once_with(
            room_id=sample_user_message.room_id,
            message_id=sample_user_message.message_id,
            requested_by_user_id=mock_user.user_id,
        )

    @pytest.mark.asyncio
    async def test_handles_execution_cancel_failure(
        self, mock_user, sample_room, sample_user_message, patch_sse_deps
    ):
        """Should return 500 if Execution cancellation raises."""
        deps = patch_sse_deps
        deps["db_service"].get_room_user_message_by_message_id.return_value = sample_user_message
        deps["db_service"].get_room_by_room_id.return_value = sample_room
        deps["execution_engine"].cancel.side_effect = Exception(
            "Execution cancel down"
        )

        with pytest.raises(HTTPException) as exc_info:
            await cancel_message(sample_user_message.message_id, mock_user)

        assert exc_info.value.status_code == 500
        assert "Execution cancel down" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_route_does_not_call_sse_cancel_directly(
        self, mock_user, sample_room, sample_user_message, patch_sse_deps
    ):
        """SSE route delegates cancellation internals to Execution."""
        deps = patch_sse_deps
        deps["db_service"].get_room_user_message_by_message_id.return_value = sample_user_message
        deps["db_service"].get_room_by_room_id.return_value = sample_room

        await cancel_message(sample_user_message.message_id, mock_user)

        deps["sse_manager"].cancel_message_and_broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_paused_agent_cleanup_failure_does_not_block_root_cancellation(
        self,
        mock_user,
        sample_room,
        sample_user_message,
        sample_agent_message_with_task,
        patch_sse_deps,
    ):
        """Paused-agent cleanup is best-effort after root cancellation is cleared."""
        deps = patch_sse_deps
        deps["db_service"].get_room_user_message_by_message_id.return_value = (
            sample_user_message
        )
        deps["db_service"].get_room_by_room_id.return_value = sample_room
        deps[
            "db_service"
        ].get_room_agent_messages_by_related_message_id.return_value = [
            sample_agent_message_with_task
        ]
        deps["execution_engine"].cancel.return_value = True

        result = await cancel_message(sample_user_message.message_id, mock_user)

        assert result["success"] is True
        deps["execution_engine"].cancel.assert_awaited_once_with(
            room_id=sample_user_message.room_id,
            message_id=sample_user_message.message_id,
            requested_by_user_id=mock_user.user_id,
        )
