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
    cancel_hitl_interaction,
    get_pending_hitl_requests,
)
from common.dto import HITLCancelCommand
from common.dto import HITLRequest as CommonHITLRequest
from execution.hitl.exceptions import HITLRoomMismatchError


def _room_ownership(owner_id):
    reader = MagicMock()
    reader.get_room_owner = AsyncMock(return_value=owner_id)
    return reader


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
                interaction_id="interaction-public-1",
                question_count=3,
                question_index=2,
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
            "interaction_id": "interaction-public-1",
            "question_count": 3,
            "question_index": 2,
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
# Cancel HITL Interaction Tests
# =============================================================================


class TestCancelHitlInteraction:
    """Tests for the version-fenced interaction cancel endpoint."""

    @pytest.mark.asyncio
    async def test_cancels_request(
        self, mock_user, mock_db_service, mock_hitl_service, sample_room
    ):
        """Should cancel HITL request."""
        interaction_id = "interaction-to-cancel"
        body = HITLCancelCommand(
            interaction_id=interaction_id,
            expected_interaction_version=4,
            client_request_id="cancel-1",
        )
        mock_db_service.get_room_by_room_id.return_value = sample_room
        mock_hitl_service.cancel_hitl_interaction = AsyncMock(return_value=5)

        result = await cancel_hitl_interaction(
            sample_room.room_id,
            interaction_id,
            body,
            mock_user,
            manager=mock_hitl_service,
            room_ownership=_room_ownership(mock_user.user_id),
        )

        assert result == {
            "status": "canceled",
            "interaction_id": interaction_id,
            "interaction_version": 5,
        }
        mock_hitl_service.cancel_hitl_interaction.assert_called_once_with(
            sample_room.room_id,
            interaction_id,
            4,
        )

    @pytest.mark.asyncio
    async def test_verifies_room_ownership_before_cancel(
        self, mock_user_2, mock_db_service, sample_room
    ):
        """Should verify room ownership before canceling."""
        interaction_id = "interaction-to-cancel"
        body = HITLCancelCommand(
            interaction_id=interaction_id,
            expected_interaction_version=1,
            client_request_id="cancel-1",
        )

        room_ownership_reader = MagicMock()
        room_ownership_reader.get_room_owner = AsyncMock(
            return_value=sample_room.room_owner_id
        )

        with pytest.raises(HTTPException) as exc_info:
            await cancel_hitl_interaction(
                sample_room.room_id,
                interaction_id,
                body,
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
        mock_hitl_service.cancel_hitl_interaction = AsyncMock(side_effect=error)
        body = HITLCancelCommand(
            interaction_id="interaction-to-cancel",
            expected_interaction_version=1,
            client_request_id="cancel-1",
        )

        with pytest.raises(HTTPException) as exc_info:
            await cancel_hitl_interaction(
                sample_room.room_id,
                "interaction-to-cancel",
                body,
                mock_user,
                manager=mock_hitl_service,
                room_ownership=_room_ownership(mock_user.user_id),
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Room mismatch"
