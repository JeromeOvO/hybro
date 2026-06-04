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

from unittest.mock import AsyncMock, MagicMock

import pytest

from execution.hitl.exceptions import (
    HITLConflictError,
    HITLNotFoundError,
    HITLRoomMismatchError,
    HITLRoutingFailedError,
)
from models.hitl import (
    HITLEventType,
    HITLPromptType,
    HITLRequest,
    HITLStatus,
)
from app_shell.hitl_service import MAX_HITL_ROUNDS, HITLService


class _AsyncCursor:
    def __init__(self, docs):
        self._docs = iter(docs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._docs)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


# =============================================================================
# HITL Service Fixtures
# =============================================================================


@pytest.fixture
def hitl_service():
    """Create a fresh HITLService instance for testing."""
    service = HITLService()
    # Reset lazy-loaded dependencies
    service._db_service = None
    service._delivery = None
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
    mock.count_pending_in_hitl_group = AsyncMock(return_value=0)
    mock.claim_hitl_group_routing = AsyncMock(return_value=True)
    mock.release_hitl_group_routing = AsyncMock(return_value=True)
    mock.get_hitl_group_requests = AsyncMock(return_value=[])
    mock.reset_last_notified_state = AsyncMock()
    mock.get_pending_continuation_on_message = AsyncMock(return_value=None)
    mock.save_continuation_on_user_message = AsyncMock(return_value=True)
    mock.get_and_clear_continuation_on_message = AsyncMock()
    mock.get_and_clear_continuation_on_user_message = AsyncMock()
    return mock


@pytest.fixture
def mock_hitl_sse_manager():
    """Create mock typed delivery port for HITL events."""
    mock = MagicMock()
    mock.emit = AsyncMock()
    return mock


# =============================================================================
# Request Input Tests
# =============================================================================


def test_infer_prompt_type_detects_approve_reject():
    from execution.hitl.detector import infer_prompt_type

    assert infer_prompt_type("Approve or reject this action").value == "confirmation"


def test_hitl_request_translator_preserves_pending_api_shape(sample_hitl_request):
    from execution.hitl.translators import model_hitl_request_to_common

    sample_hitl_request.display_message_id = "display-msg-1"
    sample_hitl_request.group_id = "group-1"
    sample_hitl_request.group_total = 2
    sample_hitl_request.group_index = 1

    common = model_hitl_request_to_common(sample_hitl_request)

    assert common.request_id == sample_hitl_request.request_id
    assert common.message_id == "display-msg-1"
    assert common.group_id == "group-1"
    assert common.group_total == 2
    assert common.group_index == 1


def test_hitl_response_translator_preserves_route_dict_shape():
    from execution.hitl.translators import hitl_response_dict_to_common

    response = hitl_response_dict_to_common(
        {
            "status": "ok",
            "request_id": "req-1",
            "reclaimed": True,
            "error": None,
        }
    )

    assert response.status == "ok"
    assert response.request_id == "req-1"
    assert response.reclaimed is True


def test_bound_hitl_service_proxy_raises_before_binding_and_forwards_after_binding():
    from execution.hitl.factory import BoundHITLServiceProxy

    proxy = BoundHITLServiceProxy()
    with pytest.raises(RuntimeError):
        attr_name = "recover_stale_processing"
        getattr(proxy, attr_name)

    target = MagicMock()
    target.recover_stale_processing = AsyncMock(return_value=3)
    proxy.bind(target)
    assert proxy.recover_stale_processing is target.recover_stale_processing


def test_legacy_hitl_singleton_is_bound_proxy():
    from app_shell.hitl_service import BoundHITLServiceProxy, hitl_service

    assert isinstance(hitl_service, BoundHITLServiceProxy)


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
        
        mock_hitl_sse_manager.emit.assert_awaited_once()
        event = mock_hitl_sse_manager.emit.await_args.args[0]
        assert event.room_id == "room-123"
        assert event.event_type == "hitl_request"

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
        
        mock_hitl_db_service.cas_update_hitl_request.assert_awaited_once_with(
            sample_hitl_request.request_id,
            expected_status=HITLStatus.PENDING.value,
            status=HITLStatus.CANCELED.value,
        )

    @pytest.mark.asyncio
    async def test_raises_404_when_request_not_found(
        self, hitl_service, mock_hitl_db_service
    ):
        """Should raise 404 when request doesn't exist."""
        hitl_service._db_service = mock_hitl_db_service
        mock_hitl_db_service.get_hitl_request.return_value = None
        
        with pytest.raises(HITLNotFoundError) as exc_info:
            await hitl_service.cancel_request("nonexistent-request")
        
        assert exc_info.value.message == "HITL request not found"

    @pytest.mark.asyncio
    async def test_raises_403_on_room_mismatch(
        self, hitl_service, mock_hitl_db_service, sample_hitl_request
    ):
        """Should raise 403 when room_id doesn't match."""
        hitl_service._db_service = mock_hitl_db_service
        
        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = request_doc
        
        with pytest.raises(HITLRoomMismatchError) as exc_info:
            await hitl_service.cancel_request(
                sample_hitl_request.request_id,
                room_id="different-room",
            )
        
        assert exc_info.value.message == "Room mismatch"

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
    async def test_cancel_uses_pending_cas_before_clearing_or_emitting(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = request_doc
        mock_hitl_db_service.cas_update_hitl_request.return_value = True

        await hitl_service.cancel_request(
            sample_hitl_request.request_id,
            room_id=sample_hitl_request.room_id,
        )

        mock_hitl_db_service.cas_update_hitl_request.assert_awaited_once_with(
            sample_hitl_request.request_id,
            expected_status=HITLStatus.PENDING.value,
            status=HITLStatus.CANCELED.value,
        )
        mock_hitl_db_service.update_hitl_request.assert_not_called()
        mock_hitl_sse_manager.emit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_does_not_clear_or_emit_when_pending_cas_loses(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = request_doc
        mock_hitl_db_service.cas_update_hitl_request.return_value = False

        await hitl_service.cancel_request(
            sample_hitl_request.request_id,
            room_id=sample_hitl_request.room_id,
        )

        mock_hitl_db_service.get_and_clear_continuation_on_message.assert_not_awaited()
        mock_hitl_db_service.get_and_clear_continuation_on_user_message.assert_not_awaited()
        mock_hitl_sse_manager.emit.assert_not_awaited()

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
        
        mock_hitl_sse_manager.emit.assert_awaited_once()
        event = mock_hitl_sse_manager.emit.await_args.args[0]
        assert event.event_type == "hitl_resolved"
        assert event.status == HITLStatus.CANCELED.value


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
        
        # Should have CAS-canceled both requests
        assert mock_hitl_db_service.cas_update_hitl_request.await_count == 2


class TestHandleResponseErrors:
    """Tests for execution-owned handle_response errors."""

    @pytest.mark.asyncio
    async def test_raises_execution_not_found_when_claim_missing_request(
        self, hitl_service, mock_hitl_db_service
    ):
        hitl_service._db_service = mock_hitl_db_service
        mock_hitl_db_service.claim_hitl_request.return_value = None
        mock_hitl_db_service.get_hitl_request.return_value = None

        with pytest.raises(HITLNotFoundError) as exc_info:
            await hitl_service.handle_response(
                room_id="room-1",
                request_id="missing-request",
                user_input="yes",
                user_id="user-1",
            )

        assert exc_info.value.message == "HITL request not found"

    @pytest.mark.asyncio
    async def test_raises_execution_conflict_when_claim_already_resolved(
        self, hitl_service, mock_hitl_db_service, sample_hitl_request
    ):
        hitl_service._db_service = mock_hitl_db_service
        mock_hitl_db_service.claim_hitl_request.return_value = None
        doc = sample_hitl_request.model_dump(mode="json")
        doc["status"] = HITLStatus.RESPONDED.value
        mock_hitl_db_service.get_hitl_request.return_value = doc

        with pytest.raises(HITLConflictError) as exc_info:
            await hitl_service.handle_response(
                room_id=sample_hitl_request.room_id,
                request_id=sample_hitl_request.request_id,
                user_input="yes",
                user_id="user-1",
            )

        assert exc_info.value.message == "Request already responded"

    @pytest.mark.asyncio
    async def test_handle_response_marks_display_message_completed(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        mock_hitl_db_service.persist_hitl_user_answer = AsyncMock()
        mock_hitl_db_service.update_agent_message_task_state = AsyncMock()

        request = sample_hitl_request.model_copy(
            update={
                "source": "agent",
                "display_message_id": "display-msg-1",
                "a2a_task_id": "task-1",
                "a2a_context_id": "ctx-1",
            }
        )
        doc = request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = doc
        mock_hitl_db_service.claim_hitl_request.return_value = doc
        mock_hitl_db_service.fenced_update_hitl_request.return_value = True
        hitl_service._handle_agent_response = AsyncMock()

        result = await hitl_service.handle_response(
            room_id=request.room_id,
            request_id=request.request_id,
            user_input="A",
            user_id="user-1",
        )

        assert result == {"status": "ok", "request_id": request.request_id}
        mock_hitl_db_service.persist_hitl_user_answer.assert_awaited_once_with(
            "display-msg-1", "A"
        )
        mock_hitl_db_service.update_agent_message_task_state.assert_awaited_once_with(
            "display-msg-1", "completed"
        )


class TestGroupedHandleResponse:
    """Tests for grouped HITL response routing."""

    @pytest.mark.asyncio
    async def test_first_group_answer_waits_for_remaining_answers(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        request = sample_hitl_request.model_copy(
            update={
                "source": "supervisor",
                "group_id": "group-1",
                "group_total": 2,
                "group_index": 0,
                "continuation_message_id": "cont-1",
            }
        )
        doc = request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = doc
        mock_hitl_db_service.claim_hitl_request.return_value = doc
        # The DB helper counts the current processing claim plus pending siblings.
        mock_hitl_db_service.count_pending_in_hitl_group.return_value = 2
        hitl_service._handle_supervisor_response = AsyncMock()

        result = await hitl_service.handle_response(
            room_id=request.room_id,
            request_id=request.request_id,
            user_input="first answer",
            user_id="user-1",
        )

        assert result == {"status": "ok", "request_id": request.request_id}
        hitl_service._handle_supervisor_response.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_last_group_answer_routes_supervisor_response(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        request = sample_hitl_request.model_copy(
            update={
                "source": "supervisor",
                "group_id": "group-1",
                "group_total": 2,
                "group_index": 1,
                "continuation_message_id": "cont-1",
            }
        )
        doc = request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = doc
        mock_hitl_db_service.claim_hitl_request.return_value = doc
        # The DB helper counts the current processing claim, so the final active
        # group member reports one remaining record.
        mock_hitl_db_service.count_pending_in_hitl_group.return_value = 1
        mock_hitl_db_service.get_hitl_group_requests.return_value = [
            {**doc, "prompt": "Q1?", "request_id": "req-1", "user_input": "first"},
            {**doc, "prompt": "Q2?", "request_id": request.request_id},
        ]
        hitl_service._handle_supervisor_response = AsyncMock()

        await hitl_service.handle_response(
            room_id=request.room_id,
            request_id=request.request_id,
            user_input="second",
            user_id="user-1",
        )

        hitl_service._handle_supervisor_response.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_concurrent_group_answer_routes_after_finalize_when_group_completes(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        request = sample_hitl_request.model_copy(
            update={
                "source": "supervisor",
                "group_id": "group-1",
                "group_total": 2,
                "group_index": 0,
                "continuation_message_id": "cont-1",
            }
        )
        doc = request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = doc
        mock_hitl_db_service.claim_hitl_request.return_value = doc
        mock_hitl_db_service.count_pending_in_hitl_group.side_effect = [2, 0]
        mock_hitl_db_service.claim_hitl_group_routing.return_value = True
        mock_hitl_db_service.get_hitl_group_requests.return_value = [
            {**doc, "prompt": "Q1?", "request_id": request.request_id},
            {**doc, "prompt": "Q2?", "request_id": "req-2", "user_input": "second"},
        ]
        hitl_service._handle_supervisor_response = AsyncMock()

        await hitl_service.handle_response(
            room_id=request.room_id,
            request_id=request.request_id,
            user_input="first",
            user_id="user-1",
        )

        mock_hitl_db_service.claim_hitl_group_routing.assert_awaited_once()
        hitl_service._handle_supervisor_response.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_group_route_failure_releases_claim_for_retry(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        request = sample_hitl_request.model_copy(
            update={
                "source": "supervisor",
                "group_id": "group-1",
                "group_total": 2,
                "group_index": 1,
                "continuation_message_id": "cont-1",
            }
        )
        doc = request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = doc
        mock_hitl_db_service.claim_hitl_request.return_value = doc
        mock_hitl_db_service.count_pending_in_hitl_group.return_value = 1
        mock_hitl_db_service.claim_hitl_group_routing.return_value = True
        mock_hitl_db_service.get_hitl_group_requests.return_value = [
            {**doc, "prompt": "Q1?", "request_id": "req-1", "user_input": "first"},
            {**doc, "prompt": "Q2?", "request_id": request.request_id},
        ]
        hitl_service._handle_supervisor_response = AsyncMock(
            side_effect=RuntimeError("transient resume failure")
        )

        with pytest.raises(HITLRoutingFailedError):
            await hitl_service.handle_response(
                room_id=request.room_id,
                request_id=request.request_id,
                user_input="second",
                user_id="user-1",
            )

        mock_hitl_db_service.release_hitl_group_routing.assert_awaited_once()
        assert (
            mock_hitl_db_service.release_hitl_group_routing.await_args.args[0]
            == "group-1"
        )

    @pytest.mark.asyncio
    async def test_room_mismatch_is_rejected_before_claim(
        self, hitl_service, mock_hitl_db_service, sample_hitl_request
    ):
        hitl_service._db_service = mock_hitl_db_service
        doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = doc

        with pytest.raises(HITLRoomMismatchError):
            await hitl_service.handle_response(
                room_id="different-room",
                request_id=sample_hitl_request.request_id,
                user_input="yes",
                user_id="user-1",
            )

        mock_hitl_db_service.claim_hitl_request.assert_not_awaited()
        mock_hitl_db_service.fenced_update_hitl_request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wrong_room_resolved_request_does_not_leak_status(
        self, hitl_service, mock_hitl_db_service, sample_hitl_request
    ):
        hitl_service._db_service = mock_hitl_db_service
        doc = sample_hitl_request.model_dump(mode="json")
        doc["status"] = HITLStatus.RESPONDED.value
        mock_hitl_db_service.get_hitl_request.return_value = doc

        with pytest.raises(HITLRoomMismatchError) as exc_info:
            await hitl_service.handle_response(
                room_id="different-room",
                request_id=sample_hitl_request.request_id,
                user_input="yes",
                user_id="user-1",
            )

        assert exc_info.value.message == "Room mismatch"
        mock_hitl_db_service.claim_hitl_request.assert_not_awaited()


# =============================================================================
# SSE Event Emission Tests
# =============================================================================


class TestEmitHitlEvent:
    """Tests for _emit_hitl_event method."""

    @pytest.mark.asyncio
    async def test_emits_input_requested_event(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        """Should emit correct data for INPUT_REQUESTED event."""
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        await hitl_service._emit_hitl_event(
            room_id=sample_hitl_request.room_id,
            event_type=HITLEventType.INPUT_REQUESTED,
            request=sample_hitl_request,
        )
        
        mock_hitl_sse_manager.emit.assert_awaited_once()
        event = mock_hitl_sse_manager.emit.await_args.args[0]

        assert event.room_id == sample_hitl_request.room_id
        assert event.event_type == "hitl_request"
        assert event.request_id == sample_hitl_request.request_id
        assert event.prompt == sample_hitl_request.prompt
        assert event.source == sample_hitl_request.source

    @pytest.mark.asyncio
    async def test_emits_status_update_event(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        """Should emit correct data for status update events."""
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        await hitl_service._emit_hitl_event(
            room_id=sample_hitl_request.room_id,
            event_type=HITLEventType.INPUT_RECEIVED,
            request=sample_hitl_request,
        )
        
        event = mock_hitl_sse_manager.emit.await_args.args[0]
        assert event.event_type == "hitl_resolved"
        assert event.status == HITLStatus.RESPONDED.value

    @pytest.mark.asyncio
    async def test_includes_error_message_on_error_event(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        """Should include error message for ERROR events."""
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager
        
        await hitl_service._emit_hitl_event(
            room_id=sample_hitl_request.room_id,
            event_type=HITLEventType.ERROR,
            request=sample_hitl_request,
            error="Something went wrong",
        )
        
        event = mock_hitl_sse_manager.emit.await_args.args[0]
        assert event.error_message == "Something went wrong"

    @pytest.mark.asyncio
    async def test_resolves_client_request_id_from_message_id_when_user_row_missing(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        """SSE payload should include client_request_id via DB resolver on message_id."""
        mock_hitl_db_service.get_room_user_message_by_message_id = AsyncMock(
            return_value=None
        )
        mock_hitl_db_service.resolve_client_request_id_for_message_id = AsyncMock(
            return_value="cr-resolved-via-message-id"
        )
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager

        req = sample_hitl_request.model_copy(
            update={"display_message_id": "test-agent-msg-001"}
        )

        await hitl_service._emit_hitl_event(
            room_id=req.room_id,
            event_type=HITLEventType.INPUT_REQUESTED,
            request=req,
        )

        event = mock_hitl_sse_manager.emit.await_args.args[0]
        assert event.message_id == "test-agent-msg-001"
        assert event.client_request_id == "cr-resolved-via-message-id"
        mock_hitl_db_service.resolve_client_request_id_for_message_id.assert_called_once_with(
            "test-agent-msg-001"
        )

    @pytest.mark.asyncio
    async def test_prefers_user_message_client_request_id_over_resolver(
        self, hitl_service, mock_hitl_db_service, mock_hitl_sse_manager, sample_hitl_request
    ):
        """When user row already has client_request_id, do not replace with resolver."""
        user_row = MagicMock()
        user_row.client_request_id = "cr-from-user-row"
        mock_hitl_db_service.get_room_user_message_by_message_id = AsyncMock(
            return_value=user_row
        )
        mock_hitl_db_service.resolve_client_request_id_for_message_id = AsyncMock(
            return_value="cr-from-resolver"
        )
        hitl_service._db_service = mock_hitl_db_service
        hitl_service._sse_manager = mock_hitl_sse_manager

        req = sample_hitl_request.model_copy(
            update={"display_message_id": "test-agent-msg-001"}
        )

        await hitl_service._emit_hitl_event(
            room_id=req.room_id,
            event_type=HITLEventType.INPUT_REQUESTED,
            request=req,
        )

        event = mock_hitl_sse_manager.emit.await_args.args[0]
        assert event.client_request_id == "cr-from-user-row"
        mock_hitl_db_service.resolve_client_request_id_for_message_id.assert_not_called()


class TestRecoverStaleProcessing:
    @pytest.mark.asyncio
    async def test_releases_stale_group_routing_claim_before_reverting_to_pending(
        self, hitl_service, mock_hitl_db_service
    ):
        stale_doc = {
            "request_id": "hitl-1",
            "status": HITLStatus.PROCESSING.value,
            "group_id": "group-1",
            "claim_id": "claim-1",
            "routing_completed_at": None,
        }
        hitl_requests = MagicMock()
        hitl_requests.find.return_value = _AsyncCursor([stale_doc])
        mock_hitl_db_service.mongo = MagicMock(
            db=MagicMock(hitl_requests=hitl_requests)
        )
        hitl_service._db_service = mock_hitl_db_service

        recovered = await hitl_service.recover_stale_processing()

        assert recovered == 1
        mock_hitl_db_service.release_hitl_group_routing.assert_awaited_once_with(
            "group-1",
            "claim-1",
        )
        mock_hitl_db_service.cas_update_hitl_request.assert_awaited_once()
        _, kwargs = mock_hitl_db_service.cas_update_hitl_request.await_args
        assert kwargs["status"] == HITLStatus.PENDING.value
        assert kwargs["claim_id"] is None
