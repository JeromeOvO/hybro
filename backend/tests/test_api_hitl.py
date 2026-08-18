"""
Unit tests for HITL (Human-in-the-Loop) API endpoints.

Tests cover:
- Responding to HITL requests
- Getting pending HITL requests
- Canceling HITL requests
- Room ownership verification
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api_gateway.routes.hitl_routes import (
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
                source=sample_hitl_request.public_source.value,
                prompt=sample_hitl_request.prompt,
                display_message_id=sample_hitl_request.display_message_id,
                client_request_id="cr-pending-hitl",
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
        assert result["requests"][0]["client_request_id"] == "cr-pending-hitl"

    @pytest.mark.asyncio
    async def test_pending_requests_omit_absent_orchestration_run_links(
        self,
        mock_user,
        mock_db_service,
        mock_hitl_service,
        sample_room,
        sample_hitl_request,
    ):
        mock_db_service.get_room_by_room_id.return_value = sample_room
        mock_hitl_service.get_pending_hitl.return_value = [
            CommonHITLRequest(
                request_id=sample_hitl_request.request_id,
                room_id=sample_hitl_request.room_id,
                user_message_id=sample_hitl_request.user_message_id,
                source=sample_hitl_request.public_source.value,
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

        pending = result["requests"][0]
        assert "orchestration_run_id" not in pending

    @pytest.mark.asyncio
    async def test_pending_requests_use_public_sse_projection(
        self,
        mock_user,
        mock_db_service,
        mock_hitl_service,
        sample_room,
    ):
        mock_db_service.get_room_by_room_id.return_value = sample_room
        mock_hitl_service.get_pending_hitl.return_value = [
            CommonHITLRequest(
                request_id="hitl-public-1",
                room_id=sample_room.room_id,
                user_message_id="user-msg-private",
                source="agent",
                prompt="Approve this action?",
                message_id="display-msg-1",
                source_step_id="step-public-1",
                agent_id="agent-public-1",
                agent_name="Researcher",
                a2a_task_id="a2a-task-private",
                a2a_context_id="a2a-context-private",
                continuation_message_id="continuation-private",
                display_message_id="display-msg-1",
                client_request_id="client-public-1",
                orchestration_run_id="run-private",
                prompt_type="choice",
                choices=["Approve", "Reject"],
                group_id="group-public-1",
                group_total=3,
                group_index=2,
                status="processing",
                expires_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                created_at=datetime(2026, 1, 2, 2, 4, 5, tzinfo=UTC),
                user_input="private answer",
                responded_at=datetime(2026, 1, 2, 2, 5, 5, tzinfo=UTC),
                responded_by_user_id="user-private",
            )
        ]

        result = await get_pending_hitl_requests(
            sample_room.room_id,
            mock_user,
            manager=mock_hitl_service,
            room_ownership=_room_ownership(mock_user.user_id),
        )

        pending = result["requests"][0]
        assert pending == {
            "request_id": "hitl-public-1",
            "message_id": "display-msg-1",
            "prompt": "Approve this action?",
            "prompt_type": "choice",
            "choices": ["Approve", "Reject"],
            "source": "agent",
            "agent_id": "agent-public-1",
            "agent_name": "Researcher",
            "source_step_id": "step-public-1",
            "group_id": "group-public-1",
            "group_total": 3,
            "group_index": 2,
            "related_message_id": "user-msg-private",
            "client_request_id": "client-public-1",
            "expires_at": "2026-01-02T03:04:05Z",
            "created_at": "2026-01-02T02:04:05Z",
        }
        assert (
            not {
                "room_id",
                "user_message_id",
                "a2a_task_id",
                "a2a_context_id",
                "continuation_message_id",
                "display_message_id",
                "orchestration_run_id",
                "status",
                "user_input",
                "responded_at",
                "responded_by_user_id",
            }
            & pending.keys()
        )

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
