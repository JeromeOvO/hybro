"""
Unit tests for HITL Service.

Tests cover:
- Creating HITL requests
- Handling user responses
- Getting pending requests
- Canceling requests
- Max rounds enforcement
- SSE event emission
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from models.hitl import (
    HITLRequest,
    HITLStatus,
    HITLPromptType,
    HITLEventType,
)
from services.hitl_service import HITLService, MAX_HITL_ROUNDS


# =============================================================================
# HITL Service Fixtures
# =============================================================================


@pytest.fixture
def hitl_service():
    """Create a fresh HITLService instance for testing."""
    service = HITLService()
    # Reset lazy-loaded dependencies
    service._db_service = None
    service._sse_manager = None
    service._a2a_service = None
    return service


@pytest.fixture
def mock_hitl_db_service():
    """Create mock database service for HITL operations."""
    mock = MagicMock()
    mock.create_hitl_request = AsyncMock(return_value=True)
    mock.get_hitl_request = AsyncMock(return_value=None)
    mock.update_hitl_request = AsyncMock(return_value=True)
    mock.get_pending_hitl_requests = AsyncMock(return_value=[])
    mock.get_pending_hitl_requests_for_message = AsyncMock(return_value=[])
    mock.count_hitl_requests_for_message = AsyncMock(return_value=0)
    mock.claim_hitl_request = AsyncMock(return_value=None)
    mock.fenced_update_hitl_request = AsyncMock(return_value=True)
    mock.cas_update_hitl_request = AsyncMock(return_value=True)
    mock.reset_last_notified_state = AsyncMock()
    mock.get_pending_continuation_on_message = AsyncMock(return_value=None)
    mock.save_continuation_on_user_message = AsyncMock(return_value=True)
    mock.get_and_clear_continuation_on_message = AsyncMock()
    mock.get_and_clear_continuation_on_user_message = AsyncMock()
    return mock


@pytest.fixture
def mock_hitl_sse_manager():
    """Create mock SSE manager for HITL events."""
    mock = MagicMock()
    mock.broadcast_to_room = AsyncMock()
    return mock


# =============================================================================
# Request Input Tests
# =============================================================================


class TestRequestInput:
    """Tests for request_input method."""

    @pytest.mark.asyncio
    async def test_creates_hitl_request(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager
    ):
        """Should create and persist HITL request."""
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Please clarify your request",
            prompt_type=HITLPromptType.TEXT,
        )
        
        assert result is not None
        assert result.room_id == "room-123"
        assert result.user_message_id == "msg-456"
        assert result.source == "supervisor"
        assert result.prompt == "Please clarify your request"
        assert result.status == HITLStatus.PENDING
        
        mock_hitl_db_service.create_hitl_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_emits_sse_event_on_creation(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager
    ):
        """Should emit SSE event when request is created."""
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="agent",
            prompt="Need more info",
            agent_id="agent-789",
            agent_name="TestAgent",
        )
        
        mock_hitl_sse_manager.broadcast_to_room.assert_called_once()
        call_args = mock_hitl_sse_manager.broadcast_to_room.call_args
        assert call_args[0][0] == "room-123"
        assert call_args[0][1] == "hitl_input_requested"

    @pytest.mark.asyncio
    async def test_returns_none_when_max_rounds_exceeded(
        self, hitl_service, mock_hitl_db_service
    ):
        """Should return None when max HITL rounds exceeded."""
        hitl_service._db_service = mock_hitl_db_service
        mock_hitl_db_service.count_hitl_requests_for_message.return_value = MAX_HITL_ROUNDS
        
        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Another clarification",
            continuation_message_id="cont-msg-123",
        )
        
        assert result is None
        mock_hitl_db_service.create_hitl_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_when_db_save_fails(
        self, hitl_service, mock_hitl_db_service
    ):
        """Should return None when database save fails."""
        hitl_service._db_service = mock_hitl_db_service
        mock_hitl_db_service.create_hitl_request.return_value = False
        
        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Test prompt",
        )
        
        assert result is None

    @pytest.mark.asyncio
    async def test_creates_request_with_choices(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager
    ):
        """Should create request with choice options."""
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        choices = ["Option A", "Option B", "Option C"]
        
        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Choose an option",
            prompt_type=HITLPromptType.CHOICE,
            choices=choices,
        )
        
        assert result.prompt_type == HITLPromptType.CHOICE
        assert result.choices == choices


# =============================================================================
# Get Pending Requests Tests
# =============================================================================


class TestGetPendingRequests:
    """Tests for get_pending_requests method."""

    @pytest.mark.asyncio
    async def test_returns_pending_requests_for_room(
        self, hitl_service, mock_hitl_db_service, sample_hitl_request
    ):
        """Should return pending requests for a room."""
        hitl_service._db_service = mock_hitl_db_service
        
        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_pending_hitl_requests.return_value = [request_doc]
        
        result = await hitl_service.get_pending_requests(sample_hitl_request.room_id)
        
        assert len(result) == 1
        assert result[0].request_id == sample_hitl_request.request_id

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_pending(
        self, hitl_service, mock_hitl_db_service
    ):
        """Should return empty list when no pending requests."""
        hitl_service._db_service = mock_hitl_db_service
        mock_hitl_db_service.get_pending_hitl_requests.return_value = []
        
        result = await hitl_service.get_pending_requests("room-123")
        
        assert result == []


class TestGetPendingRequestsForMessage:
    """Tests for get_pending_requests_for_message method."""

    @pytest.mark.asyncio
    async def test_returns_requests_for_message(
        self, hitl_service, mock_hitl_db_service, sample_hitl_request
    ):
        """Should return pending requests for a specific message."""
        hitl_service._db_service = mock_hitl_db_service
        
        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_pending_hitl_requests_for_message.return_value = [
            request_doc
        ]
        
        result = await hitl_service.get_pending_requests_for_message("msg-123")
        
        assert len(result) == 1


# =============================================================================
# Cancel Request Tests
# =============================================================================


class TestCancelRequest:
    """Tests for cancel_request method."""

    @pytest.mark.asyncio
    async def test_cancels_pending_request(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        """Should cancel a pending HITL request."""
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = request_doc
        
        await hitl_service.cancel_request(
            sample_hitl_request.request_id,
            room_id=sample_hitl_request.room_id,
        )
        
        mock_hitl_db_service.update_hitl_request.assert_called_once_with(
            sample_hitl_request.request_id, status=HITLStatus.CANCELED
        )

    @pytest.mark.asyncio
    async def test_raises_404_when_request_not_found(
        self, hitl_service, mock_hitl_db_service
    ):
        """Should raise 404 when request doesn't exist."""
        hitl_service._db_service = mock_hitl_db_service
        mock_hitl_db_service.get_hitl_request.return_value = None
        
        with pytest.raises(HTTPException) as exc_info:
            await hitl_service.cancel_request("nonexistent-request")
        
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_403_on_room_mismatch(
        self, hitl_service, mock_hitl_db_service, sample_hitl_request
    ):
        """Should raise 403 when room_id doesn't match."""
        hitl_service._db_service = mock_hitl_db_service
        
        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = request_doc
        
        with pytest.raises(HTTPException) as exc_info:
            await hitl_service.cancel_request(
                sample_hitl_request.request_id,
                room_id="different-room",
            )
        
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_noop_when_already_resolved(
        self, hitl_service, mock_hitl_db_service, sample_hitl_request
    ):
        """Should be no-op when request is already resolved."""
        hitl_service._db_service = mock_hitl_db_service
        
        # Set status to RESPONDED (already resolved)
        sample_hitl_request.status = HITLStatus.RESPONDED
        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = request_doc
        
        await hitl_service.cancel_request(sample_hitl_request.request_id)
        
        # Should not call update
        mock_hitl_db_service.update_hitl_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_emits_cancel_event(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        """Should emit SSE cancel event."""
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = request_doc
        
        await hitl_service.cancel_request(
            sample_hitl_request.request_id,
            room_id=sample_hitl_request.room_id,
        )
        
        mock_hitl_sse_manager.broadcast_to_room.assert_called_once()
        call_args = mock_hitl_sse_manager.broadcast_to_room.call_args
        assert call_args[0][1] == "hitl_status_update"


class TestCancelRequestsForMessage:
    """Tests for cancel_requests_for_message method."""

    @pytest.mark.asyncio
    async def test_cancels_all_pending_for_message(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager
    ):
        """Should cancel all pending requests for a message."""
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        # Create two pending requests
        request1 = HITLRequest(
            request_id="req-1",
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Prompt 1",
            status=HITLStatus.PENDING,
        )
        request2 = HITLRequest(
            request_id="req-2",
            room_id="room-123",
            user_message_id="msg-456",
            source="agent",
            prompt="Prompt 2",
            status=HITLStatus.PENDING,
        )
        
        mock_hitl_db_service.get_pending_hitl_requests_for_message.return_value = [
            request1.model_dump(mode="json"),
            request2.model_dump(mode="json"),
        ]
        
        # Mock get_hitl_request to return each request when queried
        def get_request_side_effect(request_id):
            if request_id == "req-1":
                return request1.model_dump(mode="json")
            elif request_id == "req-2":
                return request2.model_dump(mode="json")
            return None
        
        mock_hitl_db_service.get_hitl_request.side_effect = get_request_side_effect
        
        await hitl_service.cancel_requests_for_message("msg-456")
        
        # Should have called update for both requests
        assert mock_hitl_db_service.update_hitl_request.call_count == 2


# =============================================================================
# SSE Event Emission Tests
# =============================================================================


class TestEmitHitlEvent:
    """Tests for _emit_hitl_event method."""

    @pytest.mark.asyncio
    async def test_emits_input_requested_event(
        self, hitl_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        """Should emit correct data for INPUT_REQUESTED event."""
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        await hitl_service._emit_hitl_event(
            room_id=sample_hitl_request.room_id,
            event_type=HITLEventType.INPUT_REQUESTED,
            request=sample_hitl_request,
        )
        
        mock_hitl_sse_manager.broadcast_to_room.assert_called_once()
        call_args = mock_hitl_sse_manager.broadcast_to_room.call_args
        
        assert call_args[0][0] == sample_hitl_request.room_id
        assert call_args[0][1] == "hitl_input_requested"
        
        data = call_args[0][2]
        assert data["request_id"] == sample_hitl_request.request_id
        assert data["prompt"] == sample_hitl_request.prompt
        assert data["source"] == sample_hitl_request.source

    @pytest.mark.asyncio
    async def test_emits_status_update_event(
        self, hitl_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        """Should emit correct data for status update events."""
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        await hitl_service._emit_hitl_event(
            room_id=sample_hitl_request.room_id,
            event_type=HITLEventType.INPUT_RECEIVED,
            request=sample_hitl_request,
        )
        
        call_args = mock_hitl_sse_manager.broadcast_to_room.call_args
        assert call_args[0][1] == "hitl_status_update"
        
        data = call_args[0][2]
        assert data["status"] == HITLStatus.RESPONDED.value

    @pytest.mark.asyncio
    async def test_includes_error_message_on_error_event(
        self, hitl_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        """Should include error message for ERROR events."""
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        await hitl_service._emit_hitl_event(
            room_id=sample_hitl_request.room_id,
            event_type=HITLEventType.ERROR,
            request=sample_hitl_request,
            error="Something went wrong",
        )
        
        call_args = mock_hitl_sse_manager.broadcast_to_room.call_args
        data = call_args[0][2]
        assert data["error_message"] == "Something went wrong"
