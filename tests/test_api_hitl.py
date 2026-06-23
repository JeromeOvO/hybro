"""
Unit tests for HITL (Human-in-the-Loop) API endpoints.

Tests cover:
- Responding to HITL requests
- Getting pending HITL requests
- Canceling HITL requests
- Room ownership verification
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.hitl import (
    cancel_hitl_request,
    get_pending_hitl_requests,
    respond_to_hitl_request,
)
from common.dto import HITLRequest as CommonHITLRequest
from common.dto import HITLResponse as CommonHITLResponse
from execution.hitl.exceptions import (
    HITLConflictError,
    HITLContinuationLostError,
    HITLNotFoundError,
    HITLRoomMismatchError,
    HITLRoutingFailedError,
)
from models.hitl import HITLResponseRequest


def _room_ownership(owner_id):
    reader = MagicMock()
    reader.get_room_owner = AsyncMock(return_value=owner_id)
    return reader


# =============================================================================
# HITL Response Tests
# =============================================================================


class TestRespondToHitlRequest:
    """Tests for respond_to_hitl_request endpoint."""

    @pytest.mark.asyncio
    async def test_handles_valid_response(
        self, mock_user, mock_db_service, mock_hitl_service, sample_room
    ):
        """Should handle valid HITL response."""
        body = HITLResponseRequest(
            request_id="hitl-request-123",
            user_input="Yes, proceed with the action",
        )

        mock_db_service.get_room_by_room_id.return_value = sample_room
        mock_hitl_service.resolve_hitl.return_value = CommonHITLResponse(
            request_id="hitl-request-123",
            status="ok",
        )

        result = await respond_to_hitl_request(
            sample_room.room_id,
            body,
            mock_user,
            manager=mock_hitl_service,
            room_ownership=_room_ownership(mock_user.user_id),
        )

        assert result == {"status": "ok", "request_id": "hitl-request-123"}
        mock_hitl_service.resolve_hitl.assert_called_once_with(
            sample_room.room_id,
            "hitl-request-123",
            "Yes, proceed with the action",
            mock_user.user_id,
        )

    @pytest.mark.asyncio
    async def test_verifies_room_ownership(
        self, mock_user_2, mock_db_service, sample_room
    ):
        """Should verify room ownership before processing response."""
        body = HITLResponseRequest(
            request_id="hitl-request-123",
            user_input="Response",
        )

        room_ownership_reader = MagicMock()
        room_ownership_reader.get_room_owner = AsyncMock(
            return_value=sample_room.room_owner_id
        )

        with pytest.raises(HTTPException) as exc_info:
            await respond_to_hitl_request(
                sample_room.room_id,
                body,
                mock_user_2,
                manager=MagicMock(),
                room_ownership=room_ownership_reader,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "status_code"),
        [
            (HITLNotFoundError("HITL request not found"), 404),
            (HITLConflictError("Request already responded"), 409),
            (HITLRoomMismatchError("Room mismatch"), 403),
            (
                HITLContinuationLostError(
                    "The supervisor session has expired. Please send a new message."
                ),
                410,
            ),
            (HITLRoutingFailedError("Failed to deliver response to agent"), 502),
        ],
    )
    async def test_translates_execution_hitl_errors(
        self, error, status_code, mock_user, mock_hitl_service, sample_room
    ):
        body = HITLResponseRequest(
            request_id="hitl-request-123",
            user_input="Response",
        )
        mock_hitl_service.resolve_hitl.side_effect = error

        with pytest.raises(HTTPException) as exc_info:
            await respond_to_hitl_request(
                sample_room.room_id,
                body,
                mock_user,
                manager=mock_hitl_service,
                room_ownership=_room_ownership(mock_user.user_id),
            )

        assert exc_info.value.status_code == status_code
        assert exc_info.value.detail == error.message


# =============================================================================
# Get Pending HITL Requests Tests
# =============================================================================


class TestGetPendingHitlRequests:
    """Tests for get_pending_hitl_requests endpoint."""

    @pytest.mark.asyncio
    async def test_returns_pending_requests(
        self,
        mock_user,
        mock_db_service,
        mock_hitl_service,
        sample_room,
        sample_hitl_request,
    ):
        """Should return pending HITL requests for room."""
        mock_db_service.get_room_by_room_id.return_value = sample_room
        mock_hitl_service.get_pending_hitl.return_value = [
            CommonHITLRequest(
                request_id=sample_hitl_request.request_id,
                room_id=sample_hitl_request.room_id,
                user_message_id=sample_hitl_request.user_message_id,
                source=sample_hitl_request.source,
                prompt=sample_hitl_request.prompt,
                display_message_id=sample_hitl_request.display_message_id,
            )
        ]

        result = await get_pending_hitl_requests(
            sample_room.room_id,
            mock_user,
            manager=mock_hitl_service,
            room_ownership=_room_ownership(mock_user.user_id),
        )

        assert "requests" in result
        assert len(result["requests"]) == 1
        assert result["requests"][0]["request_id"] == sample_hitl_request.request_id

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_pending(
        self, mock_user, mock_db_service, mock_hitl_service, sample_room
    ):
        """Should return empty list when no pending requests."""
        mock_db_service.get_room_by_room_id.return_value = sample_room
        mock_hitl_service.get_pending_hitl.return_value = []

        result = await get_pending_hitl_requests(
            sample_room.room_id,
            mock_user,
            manager=mock_hitl_service,
            room_ownership=_room_ownership(mock_user.user_id),
        )

        assert result["requests"] == []


# =============================================================================
# Cancel HITL Request Tests
# =============================================================================


class TestCancelHitlRequest:
    """Tests for cancel_hitl_request endpoint."""

    @pytest.mark.asyncio
    async def test_cancels_request(
        self, mock_user, mock_db_service, mock_hitl_service, sample_room
    ):
        """Should cancel HITL request."""
        request_id = "hitl-request-to-cancel"

        mock_db_service.get_room_by_room_id.return_value = sample_room

        result = await cancel_hitl_request(
            sample_room.room_id,
            request_id,
            mock_user,
            manager=mock_hitl_service,
            room_ownership=_room_ownership(mock_user.user_id),
        )

        assert result["status"] == "canceled"
        mock_hitl_service.cancel_hitl.assert_called_once_with(
            sample_room.room_id, request_id
        )

    @pytest.mark.asyncio
    async def test_verifies_room_ownership_before_cancel(
        self, mock_user_2, mock_db_service, sample_room
    ):
        """Should verify room ownership before canceling."""
        request_id = "hitl-request-to-cancel"

        room_ownership_reader = MagicMock()
        room_ownership_reader.get_room_owner = AsyncMock(
            return_value=sample_room.room_owner_id
        )

        with pytest.raises(HTTPException) as exc_info:
            await cancel_hitl_request(
                sample_room.room_id,
                request_id,
                mock_user_2,
                manager=MagicMock(),
                room_ownership=room_ownership_reader,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_translates_cancel_execution_hitl_errors(
        self, mock_user, mock_hitl_service, sample_room
    ):
        error = HITLRoomMismatchError("Room mismatch")
        mock_hitl_service.cancel_hitl.side_effect = error

        with pytest.raises(HTTPException) as exc_info:
            await cancel_hitl_request(
                sample_room.room_id,
                "hitl-request-to-cancel",
                mock_user,
                manager=mock_hitl_service,
                room_ownership=_room_ownership(mock_user.user_id),
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Room mismatch"
