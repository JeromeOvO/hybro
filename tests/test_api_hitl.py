"""
Unit tests for HITL (Human-in-the-Loop) API endpoints.

Tests cover:
- Responding to HITL requests
- Getting pending HITL requests
- Canceling HITL requests
- Room ownership verification
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from api.hitl import (
    respond_to_hitl_request,
    get_pending_hitl_requests,
    cancel_hitl_request,
)
from models.hitl import HITLResponseRequest, HITLRequest, HITLStatus
from common.dto import HITLRequest as CommonHITLRequest, HITLResponse as CommonHITLResponse
from tests.conftest import PATCH


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
        
        with patch(PATCH["hitl.verify_room_ownership"], AsyncMock()):
            with patch("api.hitl.hitl_manager", mock_hitl_service):
                result = await respond_to_hitl_request(
                    sample_room.room_id, body, mock_user
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
        
        mock_db_service.get_room_by_room_id.return_value = sample_room
        
        with patch(PATCH["room_center.db_service"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await respond_to_hitl_request(
                    sample_room.room_id, body, mock_user_2
                )
        
        assert exc_info.value.status_code == 403


# =============================================================================
# Get Pending HITL Requests Tests
# =============================================================================


class TestGetPendingHitlRequests:
    """Tests for get_pending_hitl_requests endpoint."""

    @pytest.mark.asyncio
    async def test_returns_pending_requests(
        self, mock_user, mock_db_service, mock_hitl_service, 
        sample_room, sample_hitl_request
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
        
        with patch(PATCH["hitl.verify_room_ownership"], AsyncMock()):
            with patch("api.hitl.hitl_manager", mock_hitl_service):
                result = await get_pending_hitl_requests(
                    sample_room.room_id, mock_user
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
        
        with patch(PATCH["hitl.verify_room_ownership"], AsyncMock()):
            with patch("api.hitl.hitl_manager", mock_hitl_service):
                result = await get_pending_hitl_requests(
                    sample_room.room_id, mock_user
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
        
        with patch(PATCH["hitl.verify_room_ownership"], AsyncMock()):
            with patch("api.hitl.hitl_manager", mock_hitl_service):
                result = await cancel_hitl_request(
                    sample_room.room_id, request_id, mock_user
                )
        
        assert result["status"] == "canceled"
        mock_hitl_service.cancel_hitl.assert_called_once_with(sample_room.room_id, request_id)

    @pytest.mark.asyncio
    async def test_verifies_room_ownership_before_cancel(
        self, mock_user_2, mock_db_service, sample_room
    ):
        """Should verify room ownership before canceling."""
        request_id = "hitl-request-to-cancel"
        
        mock_db_service.get_room_by_room_id.return_value = sample_room
        
        with patch(PATCH["room_center.db_service"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await cancel_hitl_request(
                    sample_room.room_id, request_id, mock_user_2
                )
        
        assert exc_info.value.status_code == 403
