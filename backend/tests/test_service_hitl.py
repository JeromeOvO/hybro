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

from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from execution.hitl.exceptions import (
    HITLConflictError,
    HITLNotFoundError,
    HITLRequestProjectionError,
    HITLRoomMismatchError,
    HITLRoutingFailedError,
)
from execution.hitl.service import MAX_HITL_ROUNDS, HITLService
from execution.orchestration.run_store import InMemoryOrchestrationRunStore
from models.hitl import (
    HITLEventType,
    HITLPromptType,
    HITLRequest,
    HITLStatus,
)
from models.orchestration import OrchestrationRunState, OrchestrationStatus


async def _iter_docs(docs):
    for doc in docs:
        yield doc


# =============================================================================
# HITL Service Fixtures
# =============================================================================


@pytest.fixture
def hitl_service():
    """Create a fresh HITLService instance for testing."""
    service = HITLService()
    # Reset lazy-loaded dependencies
    service._persistence = None
    service._delivery = None
    service._agent_reply = None
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
    mock.find_pending_hitl_request_for_agent_message = AsyncMock(return_value=None)
    mock.create_or_reuse_pending_hitl_request = AsyncMock(return_value=None)
    mock.persist_pending_hitl_on_agent_message = AsyncMock(return_value=True)
    mock.update_agent_message_task_state = AsyncMock(return_value=True)
    mock.persist_hitl_request_id_on_message = AsyncMock(return_value=True)
    mock.persist_hitl_user_answer = AsyncMock(return_value=True)
    mock.persist_hitl_group_metadata = AsyncMock(return_value=True)
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
    mock.iter_stale_processing_hitl_requests = MagicMock(return_value=_iter_docs([]))
    return mock


@pytest.fixture
def mock_hitl_delivery():
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
    sample_hitl_request.client_request_id = "cr-hitl-1"

    common = model_hitl_request_to_common(sample_hitl_request)

    assert common.request_id == sample_hitl_request.request_id
    assert common.message_id == "display-msg-1"
    assert common.client_request_id == "cr-hitl-1"
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


def test_bound_hitl_proxy_class_is_available_without_global_singleton():
    from execution.hitl.service import BoundHITLServiceProxy

    proxy = BoundHITLServiceProxy()
    assert proxy._service is None


class TestRequestInput:
    """Tests for request_input method."""

    @pytest.mark.asyncio
    async def test_creates_hitl_request(
        self, hitl_service, mock_hitl_db_service, mock_hitl_delivery
    ):
        """Should create and persist HITL request."""
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

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
    async def test_supervisor_projection_persists_request_id_on_display_message(
        self, hitl_service, mock_hitl_db_service, mock_hitl_delivery
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Please clarify your request",
            display_message_id="supervisor-display-msg",
        )

        assert result is not None
        mock_hitl_db_service.persist_hitl_request_id_on_message.assert_awaited_once_with(
            "supervisor-display-msg",
            result.request_id,
        )

    @pytest.mark.asyncio
    async def test_supervisor_request_reuses_deterministic_request_id(
        self, hitl_service, mock_hitl_db_service, mock_hitl_delivery
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        existing = HITLRequest(
            request_id="run-1:step-1:supervisor-hitl-1",
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Please clarify your request",
            continuation_message_id="msg-456",
            display_message_id="clarifier-1",
        )
        mock_hitl_db_service.create_hitl_request.return_value = False
        mock_hitl_db_service.get_hitl_request.return_value = existing.model_dump(
            mode="json",
            exclude_none=True,
        )

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Please clarify your request",
            request_id=existing.request_id,
            continuation_message_id="msg-456",
            display_message_id="clarifier-1",
        )

        assert result is not None
        assert result.request_id == existing.request_id
        mock_hitl_db_service.get_hitl_request.assert_awaited_once_with(
            existing.request_id
        )
        mock_hitl_db_service.count_hitl_requests_for_message.assert_not_awaited()
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_legacy_request_omits_absent_v2_run_links_when_persisted(
        self, hitl_service, mock_hitl_db_service, mock_hitl_delivery
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Please clarify your request",
            prompt_type=HITLPromptType.TEXT,
        )

        persisted_doc = mock_hitl_db_service.create_hitl_request.await_args.args[0]
        assert "orchestration_run_id" not in persisted_doc
        assert "orchestration_schema_version" not in persisted_doc

    @pytest.mark.asyncio
    async def test_emits_sse_event_on_creation(
        self, hitl_service, mock_hitl_db_service, mock_hitl_delivery
    ):
        """Should emit SSE event when request is created."""

        async def create_or_reuse_pending_hitl_request(request_data):
            return request_data, True

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="agent",
            prompt="Need more info",
            agent_id="agent-789",
            agent_name="TestAgent",
            continuation_message_id="agent-msg-789",
        )

        mock_hitl_delivery.emit.assert_awaited_once()
        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.room_id == "room-123"
        assert event.event_type == "hitl_request"
        assert event.message_id == "agent-msg-789"

    @pytest.mark.asyncio
    async def test_returns_request_when_sse_emit_fails(
        self, hitl_service, mock_hitl_db_service, mock_hitl_delivery
    ):
        hitl_service._persistence = mock_hitl_db_service
        mock_hitl_delivery.emit = AsyncMock(
            side_effect=RuntimeError("temporary transport failure")
        )
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Please clarify your request",
            prompt_type=HITLPromptType.TEXT,
        )

        assert result is not None
        assert result.request_id
        assert result.status == HITLStatus.PENDING
        mock_hitl_delivery.emit.assert_awaited_once()
        assert mock_hitl_db_service.create_hitl_request.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_max_rounds_exceeded(
        self, hitl_service, mock_hitl_db_service
    ):
        """Should return None when max HITL rounds exceeded."""
        hitl_service._persistence = mock_hitl_db_service
        mock_hitl_db_service.count_hitl_requests_for_message.return_value = (
            MAX_HITL_ROUNDS
        )

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
        hitl_service._persistence = mock_hitl_db_service
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
        self, hitl_service, mock_hitl_db_service, mock_hitl_delivery
    ):
        """Should create request with choice options."""
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

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

    @pytest.mark.asyncio
    async def test_agent_request_input_sanitizes_prompt_type_choices_and_public_payloads(
        self, hitl_service, mock_hitl_db_service, mock_hitl_delivery
    ):
        private_prompt = "PRIVATE_SENTINEL_agent_hitl_prompt"
        private_choice = "PRIVATE_SENTINEL_agent_hitl_choice"
        generic_prompt = "The agent needs additional information."
        persisted_docs = []

        async def create_or_reuse_pending_hitl_request(request_data):
            persisted_docs.append(request_data)
            return request_data, True

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt=private_prompt,
            prompt_type=HITLPromptType.CHOICE,
            choices=[private_choice, "Approve"],
            agent_id="agent-broker",
            a2a_task_id="a2a-task-1",
            a2a_context_id="a2a-context-1",
            continuation_message_id="agent-paused-msg",
            display_message_id="agent-paused-msg",
        )

        assert result is not None
        assert result.prompt == generic_prompt
        assert result.prompt_type == HITLPromptType.TEXT
        assert result.choices is None
        assert persisted_docs[0]["prompt"] == generic_prompt
        assert persisted_docs[0]["prompt_type"] == HITLPromptType.TEXT.value
        assert "choices" not in persisted_docs[0]
        projection_kwargs = (
            mock_hitl_db_service.persist_pending_hitl_on_agent_message.await_args.kwargs
        )
        assert projection_kwargs["prompt"] == generic_prompt
        assert projection_kwargs["prompt_type"] == HITLPromptType.TEXT
        assert projection_kwargs["choices"] is None
        event = mock_hitl_delivery.emit.await_args.args[0]
        serialized = event.model_dump_json()
        assert event.prompt == generic_prompt
        assert private_prompt not in serialized
        assert private_choice not in serialized

    @pytest.mark.asyncio
    async def test_agent_hitl_projection_persists_all_display_message_metadata(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        """Agent input-required HITL must be projected onto the display message."""

        async def create_or_reuse_pending_hitl_request(request_data):
            return request_data, True

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        mock_hitl_db_service.persist_pending_hitl_on_agent_message = AsyncMock(
            return_value=True
        )
        mock_hitl_db_service.update_agent_message_task_state = AsyncMock(
            return_value=True
        )
        mock_hitl_db_service.persist_hitl_user_answer = AsyncMock(return_value=True)
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need policy effective date",
            prompt_type=HITLPromptType.TEXT,
            choices=None,
            agent_id="agent-broker",
            agent_name="Cyber Broker Agent",
            a2a_task_id="a2a-task-1",
            a2a_context_id="a2a-context-1",
            continuation_message_id="agent-continuation-msg",
            display_message_id="agent-display-msg",
        )

        assert result is not None
        mock_hitl_db_service.find_pending_hitl_request_for_agent_message.assert_not_awaited()
        mock_hitl_db_service.create_or_reuse_pending_hitl_request.assert_awaited_once()
        create_call = (
            mock_hitl_db_service.create_or_reuse_pending_hitl_request.await_args
        )
        assert create_call.args[0]["display_message_id"] == "agent-display-msg"
        assert (
            create_call.args[0]["continuation_message_id"] == "agent-continuation-msg"
        )
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_awaited_once_with(
            "agent-display-msg",
            request_id=result.request_id,
            prompt="The agent needs additional information.",
            prompt_type=HITLPromptType.TEXT,
            choices=None,
            a2a_task_id="a2a-task-1",
            a2a_context_id="a2a-context-1",
            group_id=None,
            group_total=None,
            group_index=None,
        )

    @pytest.mark.asyncio
    async def test_agent_hitl_projection_failure_returns_none_without_emit(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        """Agent HITL request creation must fail closed if projection fails."""

        async def create_or_reuse_pending_hitl_request(request_data):
            return request_data, True

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        mock_hitl_db_service.persist_pending_hitl_on_agent_message = AsyncMock(
            return_value=False
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need policy effective date",
            prompt_type=HITLPromptType.TEXT,
            agent_id="agent-broker",
            a2a_task_id="a2a-task-1",
            a2a_context_id="a2a-context-1",
            continuation_message_id="agent-continuation-msg",
            display_message_id="agent-display-msg",
        )

        assert result is None
        mock_hitl_db_service.create_or_reuse_pending_hitl_request.assert_awaited_once()
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_awaited_once()
        create_call = (
            mock_hitl_db_service.create_or_reuse_pending_hitl_request.await_args
        )
        request_id = create_call.args[0]["request_id"]
        mock_hitl_db_service.update_hitl_request.assert_awaited_once_with(
            request_id,
            status=HITLStatus.CANCELED.value,
            error_message="failed_to_project_agent_message",
        )
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_agent_hitl_projection_exception_is_compensated_before_emit(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        """Agent HITL projection exceptions should be compensated and return None."""

        async def create_or_reuse_pending_hitl_request(request_data):
            return request_data, True

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        mock_hitl_db_service.persist_pending_hitl_on_agent_message = AsyncMock(
            side_effect=RuntimeError("projection failed")
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need policy effective date",
            prompt_type=HITLPromptType.TEXT,
            agent_id="agent-broker",
            a2a_task_id="a2a-task-1",
            a2a_context_id="a2a-context-1",
            continuation_message_id="agent-continuation-msg",
            display_message_id="agent-display-msg",
        )

        assert result is None
        mock_hitl_db_service.create_or_reuse_pending_hitl_request.assert_awaited_once()
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_awaited_once()
        create_call = (
            mock_hitl_db_service.create_or_reuse_pending_hitl_request.await_args
        )
        request_id = create_call.args[0]["request_id"]
        mock_hitl_db_service.update_hitl_request.assert_awaited_once_with(
            request_id,
            status=HITLStatus.CANCELED.value,
            error_message="failed_to_project_agent_message",
        )
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_supervisor_hitl_projection_exception_is_compensated(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        """Supervisor projection exceptions should be compensated and return None."""

        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        mock_hitl_db_service.update_agent_message_task_state = AsyncMock(
            side_effect=RuntimeError("projection failed")
        )

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Need confirmation",
            prompt_type=HITLPromptType.CHOICE,
            choices=["yes", "no"],
            continuation_message_id="user-msg-456",
            display_message_id="supervisor-msg-456",
        )

        assert result is None
        mock_hitl_db_service.create_hitl_request.assert_awaited_once()
        mock_hitl_db_service.update_agent_message_task_state.assert_awaited_once_with(
            "supervisor-msg-456",
            "input-required",
        )
        request_doc = mock_hitl_db_service.create_hitl_request.await_args.args[0]
        mock_hitl_db_service.update_hitl_request.assert_awaited_once_with(
            request_doc["request_id"],
            status=HITLStatus.CANCELED.value,
            error_message="failed_to_project_supervisor_message",
        )
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_supervisor_hitl_projection_false_update_is_treated_as_failure(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        mock_hitl_db_service.update_agent_message_task_state = AsyncMock(
            side_effect=[False, True]
        )
        mock_hitl_db_service.persist_hitl_user_answer = AsyncMock(return_value=True)

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Need confirmation",
            prompt_type=HITLPromptType.CHOICE,
            choices=["yes", "no"],
            continuation_message_id="user-msg-456",
            display_message_id="supervisor-msg-456",
        )

        assert result is None
        mock_hitl_db_service.create_hitl_request.assert_awaited_once()
        request_doc = mock_hitl_db_service.create_hitl_request.await_args.args[0]
        mock_hitl_db_service.update_hitl_request.assert_awaited_once_with(
            request_doc["request_id"],
            status=HITLStatus.CANCELED.value,
            error_message="failed_to_project_supervisor_message",
        )
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_supervisor_hitl_projection_false_user_answer_is_treated_as_failure(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        mock_hitl_db_service.update_agent_message_task_state = AsyncMock(return_value=True)
        mock_hitl_db_service.persist_hitl_user_answer = AsyncMock(
            side_effect=[False, True]
        )

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Need confirmation",
            prompt_type=HITLPromptType.CHOICE,
            choices=["yes", "no"],
            continuation_message_id="user-msg-456",
            display_message_id="supervisor-msg-456",
        )

        assert result is None
        request_doc = mock_hitl_db_service.create_hitl_request.await_args.args[0]
        mock_hitl_db_service.update_hitl_request.assert_awaited_once_with(
            request_doc["request_id"],
            status=HITLStatus.CANCELED.value,
            error_message="failed_to_project_supervisor_message",
        )
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_supervisor_hitl_projection_failure_reverts_input_required_display_state(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        mock_hitl_db_service.update_agent_message_task_state = AsyncMock(
            return_value=True
        )
        mock_hitl_db_service.persist_hitl_user_answer = AsyncMock(
            side_effect=[RuntimeError("answer projection failed"), True]
        )

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Need confirmation",
            prompt_type=HITLPromptType.CHOICE,
            choices=["yes", "no"],
            continuation_message_id="user-msg-456",
            display_message_id="supervisor-msg-456",
        )

        assert result is None
        request_doc = mock_hitl_db_service.create_hitl_request.await_args.args[0]
        mock_hitl_db_service.update_hitl_request.assert_awaited_once_with(
            request_doc["request_id"],
            status=HITLStatus.CANCELED.value,
            error_message="failed_to_project_supervisor_message",
        )
        mock_hitl_db_service.update_agent_message_task_state.assert_any_await(
            "supervisor-msg-456",
            "input-required",
        )
        mock_hitl_db_service.update_agent_message_task_state.assert_any_await(
            "supervisor-msg-456",
            "canceled",
        )
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_supervisor_hitl_group_projection_failure_clears_group_metadata(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        mock_hitl_db_service.persist_hitl_group_metadata = AsyncMock(
            side_effect=[False, True]
        )

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Need confirmation",
            prompt_type=HITLPromptType.CHOICE,
            choices=["yes", "no"],
            continuation_message_id="user-msg-456",
            display_message_id="supervisor-msg-456",
            group_id="group-1",
            group_total=2,
            group_index=0,
        )

        assert result is None
        assert mock_hitl_db_service.persist_hitl_group_metadata.await_count == 2
        clear_call = (
            mock_hitl_db_service.persist_hitl_group_metadata.await_args_list[-1]
        )
        assert clear_call.kwargs == {
            "group_id": None,
            "group_total": None,
            "group_index": None,
        }
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_supervisor_hitl_projection_compensation_failure_raises_request_id_error(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        mock_hitl_db_service.update_agent_message_task_state = AsyncMock(return_value=False)
        mock_hitl_db_service.update_hitl_request = AsyncMock(return_value=False)

        with pytest.raises(HITLRequestProjectionError) as exc_info:
            await hitl_service.request_input(
                room_id="room-123",
                user_message_id="msg-456",
                source="supervisor",
                prompt="Need confirmation",
                prompt_type=HITLPromptType.CHOICE,
                choices=["yes", "no"],
                continuation_message_id="user-msg-456",
                display_message_id="supervisor-msg-456",
            )

        request_doc = mock_hitl_db_service.create_hitl_request.await_args.args[0]
        assert exc_info.value.request_id == request_doc["request_id"]

    @pytest.mark.asyncio
    async def test_supervisor_hitl_rollback_failure_raises_request_id_error(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        mock_hitl_db_service.update_agent_message_task_state = AsyncMock(
            side_effect=[True, False]
        )
        mock_hitl_db_service.persist_hitl_user_answer = AsyncMock(return_value=False)

        with pytest.raises(HITLRequestProjectionError) as exc_info:
            await hitl_service.request_input(
                room_id="room-123",
                user_message_id="msg-456",
                source="supervisor",
                prompt="Need confirmation",
                prompt_type=HITLPromptType.CHOICE,
                choices=["yes", "no"],
                continuation_message_id="user-msg-456",
                display_message_id="supervisor-msg-456",
            )

        request_doc = mock_hitl_db_service.create_hitl_request.await_args.args[0]
        assert exc_info.value.request_id == request_doc["request_id"]
        mock_hitl_db_service.update_agent_message_task_state.assert_any_await(
            "supervisor-msg-456",
            "canceled",
        )

    @pytest.mark.asyncio
    async def test_supervisor_hitl_emit_failure_does_not_fail_request_input(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        hitl_service._persistence = mock_hitl_db_service
        mock_hitl_delivery.emit = AsyncMock(side_effect=RuntimeError("emit failed"))
        hitl_service._delivery = mock_hitl_delivery
        mock_hitl_db_service.create_hitl_request = AsyncMock(return_value=True)

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Need confirmation",
            prompt_type=HITLPromptType.CHOICE,
            choices=["yes", "no"],
            continuation_message_id="user-msg-456",
            display_message_id="supervisor-msg-456",
        )

        assert result is not None
        assert result.source == "supervisor"
        mock_hitl_db_service.create_hitl_request.assert_awaited_once()
        mock_hitl_delivery.emit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_hitl_projection_false_cancellation_failure_raises_request_id_error(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        async def create_or_reuse_pending_hitl_request(request_data):
            return request_data, True

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        mock_hitl_db_service.persist_pending_hitl_on_agent_message = AsyncMock(
            return_value=False
        )
        mock_hitl_db_service.update_hitl_request = AsyncMock(return_value=False)
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        with pytest.raises(HITLRequestProjectionError) as exc_info:
            await hitl_service.request_input(
                room_id="room-123",
                user_message_id="msg-456",
                source="agent",
                prompt="Need confirmation",
                prompt_type=HITLPromptType.CHOICE,
                choices=["yes", "no"],
                agent_id="agent-1",
                continuation_message_id="agent-msg-456",
            )

        request_doc = mock_hitl_db_service.create_or_reuse_pending_hitl_request.await_args.args[0]
        assert exc_info.value.request_id == request_doc["request_id"]

    @pytest.mark.asyncio
    async def test_reused_agent_hitl_projection_exception_raises_request_id_error(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        """Projection exceptions on reused agent HITL must surface the active request."""

        async def create_or_reuse_pending_hitl_request(request_data):
            existing = dict(request_data)
            existing["request_id"] = "existing-request"
            return existing, False

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        mock_hitl_db_service.persist_pending_hitl_on_agent_message = AsyncMock(
            side_effect=RuntimeError("projection failed")
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        with pytest.raises(HITLRequestProjectionError) as exc_info:
            await hitl_service.request_input(
                room_id="room-123",
                user_message_id="user-msg-456",
                source="agent",
                prompt="Need policy effective date",
                prompt_type=HITLPromptType.TEXT,
                agent_id="agent-broker",
                a2a_task_id="a2a-task-1",
                a2a_context_id="a2a-context-1",
                continuation_message_id="agent-continuation-msg",
                display_message_id="agent-display-msg",
            )

        assert exc_info.value.request_id == "existing-request"
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_awaited_once()
        mock_hitl_db_service.update_hitl_request.assert_not_awaited()
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reused_agent_hitl_projection_failure_raises_request_id_error(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        """Projection failure for reused HITL must surface the active request."""

        async def create_or_reuse_pending_hitl_request(request_data):
            existing = dict(request_data)
            existing["request_id"] = "existing-request"
            return existing, False

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        mock_hitl_db_service.persist_pending_hitl_on_agent_message = AsyncMock(
            return_value=False
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        with pytest.raises(HITLRequestProjectionError) as exc_info:
            await hitl_service.request_input(
                room_id="room-123",
                user_message_id="user-msg-456",
                source="agent",
                prompt="Need policy effective date",
                prompt_type=HITLPromptType.TEXT,
                agent_id="agent-broker",
                a2a_task_id="a2a-task-1",
                a2a_context_id="a2a-context-1",
                continuation_message_id="agent-continuation-msg",
                display_message_id="agent-display-msg",
            )

        assert exc_info.value.request_id == "existing-request"
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_awaited_once()
        mock_hitl_db_service.update_hitl_request.assert_not_awaited()
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reused_agent_hitl_projects_and_emits_persisted_display_message(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        """Reused agent HITL must use persisted display identity as source of truth."""

        async def create_or_reuse_pending_hitl_request(request_data):
            existing = dict(request_data)
            existing["request_id"] = "existing-request"
            existing["display_message_id"] = "existing-display-msg"
            return existing, False

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        mock_hitl_db_service.persist_pending_hitl_on_agent_message = AsyncMock(
            return_value=True
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need policy effective date",
            prompt_type=HITLPromptType.TEXT,
            agent_id="agent-broker",
            a2a_task_id="a2a-task-1",
            a2a_context_id="a2a-context-1",
            continuation_message_id="agent-continuation-msg",
            display_message_id="retry-display-msg",
        )

        assert result is not None
        assert result.request_id == "existing-request"
        assert result.display_message_id == "existing-display-msg"
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_awaited_once()
        projection_call = (
            mock_hitl_db_service.persist_pending_hitl_on_agent_message.await_args
        )
        assert projection_call.args[0] == "existing-display-msg"
        mock_hitl_delivery.emit.assert_awaited_once()
        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.request_id == "existing-request"
        assert event.message_id == "existing-display-msg"

    @pytest.mark.asyncio
    async def test_agent_hitl_persists_resolved_client_request_id(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        docs = []

        async def create_or_reuse_pending_hitl_request(request_data):
            docs.append(request_data)
            return request_data, True

        mock_hitl_db_service.get_room_user_message_by_message_id = AsyncMock(
            return_value=None
        )
        mock_hitl_db_service.resolve_client_request_id_for_message_id = AsyncMock(
            return_value="cr-agent-hitl"
        )
        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need revenue",
            continuation_message_id="agent-msg-789",
        )

        assert result is not None
        assert result.client_request_id == "cr-agent-hitl"
        assert docs[0]["client_request_id"] == "cr-agent-hitl"
        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.client_request_id == "cr-agent-hitl"
        mock_hitl_db_service.resolve_client_request_id_for_message_id.assert_awaited_once_with(
            "agent-msg-789"
        )

    @pytest.mark.asyncio
    async def test_agent_hitl_ignores_client_request_id_resolver_failure(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        async def create_or_reuse_pending_hitl_request(request_data):
            return request_data, True

        mock_hitl_db_service.get_room_user_message_by_message_id = AsyncMock(
            return_value=None
        )
        mock_hitl_db_service.resolve_client_request_id_for_message_id = AsyncMock(
            side_effect=RuntimeError("resolver unavailable")
        )
        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need revenue",
            continuation_message_id="agent-msg-789",
        )

        assert result is not None
        assert result.client_request_id is None
        mock_hitl_db_service.create_or_reuse_pending_hitl_request.assert_awaited_once()
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_awaited_once()
        mock_hitl_delivery.emit.assert_awaited_once()
        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.client_request_id is None

    @pytest.mark.asyncio
    async def test_reused_agent_hitl_backfills_resolved_client_request_id(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        existing_doc = {
            "request_id": "hitl-reused",
            "room_id": "room-123",
            "user_message_id": "user-msg-456",
            "source": "agent",
            "prompt": "Need revenue",
            "prompt_type": HITLPromptType.TEXT.value,
            "choices": None,
            "agent_id": "agent-broker",
            "agent_name": "Cyber Broker Agent",
            "a2a_task_id": "a2a-task-1",
            "a2a_context_id": "a2a-context-1",
            "continuation_message_id": "agent-paused-msg",
            "display_message_id": "agent-paused-msg",
            "status": HITLStatus.PENDING.value,
            "expires_at": "2026-07-03T00:00:00Z",
            "created_at": "2026-07-02T00:00:00Z",
        }

        async def create_or_reuse_pending_hitl_request(_request_data):
            return dict(existing_doc), False

        mock_hitl_db_service.get_room_user_message_by_message_id = AsyncMock(
            return_value=None
        )
        mock_hitl_db_service.resolve_client_request_id_for_message_id = AsyncMock(
            return_value="cr-reused-hitl"
        )
        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need revenue",
            continuation_message_id="agent-paused-msg",
            display_message_id="agent-paused-msg",
        )

        assert result is not None
        assert result.request_id == "hitl-reused"
        assert result.client_request_id == "cr-reused-hitl"
        mock_hitl_db_service.update_hitl_request.assert_awaited_once_with(
            "hitl-reused",
            prompt="The agent needs additional information.",
            prompt_type=HITLPromptType.TEXT.value,
            choices=None,
            client_request_id="cr-reused-hitl",
        )
        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.client_request_id == "cr-reused-hitl"

    @pytest.mark.asyncio
    async def test_reused_agent_hitl_fails_when_sanitize_backfill_is_not_persisted(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        private_prompt = "PRIVATE_SENTINEL_failed_hitl_sanitize_backfill"
        existing_doc = {
            "request_id": "hitl-reused",
            "room_id": "room-123",
            "user_message_id": "user-msg-456",
            "source": "agent",
            "prompt": private_prompt,
            "prompt_type": HITLPromptType.CHOICE.value,
            "choices": [private_prompt],
            "agent_id": "agent-broker",
            "continuation_message_id": "agent-paused-msg",
            "display_message_id": "agent-paused-msg",
            "status": HITLStatus.PENDING.value,
        }
        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            return_value=(existing_doc, False)
        )
        mock_hitl_db_service.update_hitl_request = AsyncMock(return_value=False)
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        with pytest.raises(HITLRequestProjectionError) as exc_info:
            await hitl_service.request_input(
                room_id="room-123",
                user_message_id="user-msg-456",
                source="agent",
                prompt=private_prompt,
                prompt_type=HITLPromptType.CHOICE,
                choices=[private_prompt],
                continuation_message_id="agent-paused-msg",
                display_message_id="agent-paused-msg",
            )

        assert exc_info.value.request_id == "hitl-reused"
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_not_awaited()
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_agent_hitl_without_message_identity_does_not_create_or_emit(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need deductible amount",
            agent_id="agent-broker",
            agent_name="Cyber Broker Agent",
            a2a_task_id="a2a-task-1",
            a2a_context_id="a2a-context-1",
        )

        assert result is None
        mock_hitl_db_service.find_pending_hitl_request_for_agent_message.assert_not_awaited()
        mock_hitl_db_service.create_or_reuse_pending_hitl_request.assert_not_awaited()
        mock_hitl_db_service.create_hitl_request.assert_not_awaited()
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_not_awaited()
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_agent_hitl_uses_continuation_message_as_display_when_display_missing(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        async def create_or_reuse_pending_hitl_request(request_data):
            return request_data, True

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need company revenue",
            continuation_message_id="agent-paused-msg",
        )

        assert result is not None
        assert result.display_message_id == "agent-paused-msg"
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_awaited_once()
        projection_call = (
            mock_hitl_db_service.persist_pending_hitl_on_agent_message.await_args
        )
        assert projection_call.args == ("agent-paused-msg",)
        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.message_id == "agent-paused-msg"

    @pytest.mark.asyncio
    async def test_agent_hitl_reuses_existing_pending_request_for_same_agent_message(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        events = []
        existing_doc = {
            "request_id": "hitl-existing",
            "room_id": "room-123",
            "user_message_id": "user-msg-456",
            "source": "agent",
            "prompt": "Need company revenue",
            "prompt_type": HITLPromptType.TEXT.value,
            "choices": None,
            "agent_id": "agent-broker",
            "agent_name": "Cyber Broker Agent",
            "a2a_task_id": "a2a-task-1",
            "a2a_context_id": "a2a-context-1",
            "continuation_message_id": "agent-paused-msg",
            "display_message_id": "agent-paused-msg",
            "status": HITLStatus.PENDING.value,
            "expires_at": "2026-07-03T00:00:00Z",
            "created_at": "2026-07-02T00:00:00Z",
        }

        async def create_or_reuse_pending_hitl_request(request_data):
            events.append(("reuse", request_data["display_message_id"]))
            return dict(existing_doc), False

        async def persist_pending_hitl_on_agent_message(_message_id, **kwargs):
            events.append(("project", kwargs["request_id"]))
            return True

        async def emit(event):
            events.append(("emit", event.request_id))

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        mock_hitl_db_service.persist_pending_hitl_on_agent_message = AsyncMock(
            side_effect=persist_pending_hitl_on_agent_message
        )
        mock_hitl_delivery.emit = AsyncMock(side_effect=emit)
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        first = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need company revenue",
            agent_id="agent-broker",
            agent_name="Cyber Broker Agent",
            a2a_task_id="a2a-task-1",
            a2a_context_id="a2a-context-1",
            continuation_message_id="agent-paused-msg",
            display_message_id="agent-paused-msg",
        )
        second = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need company revenue",
            agent_id="agent-broker",
            agent_name="Cyber Broker Agent",
            a2a_task_id="a2a-task-1",
            a2a_context_id="a2a-context-1",
            continuation_message_id="agent-paused-msg",
            display_message_id="agent-paused-msg",
        )

        assert first is not None
        assert second is not None
        assert first.request_id == "hitl-existing"
        assert second.request_id == "hitl-existing"
        mock_hitl_db_service.create_hitl_request.assert_not_awaited()
        assert events == [
            ("reuse", "agent-paused-msg"),
            ("project", "hitl-existing"),
            ("emit", "hitl-existing"),
            ("reuse", "agent-paused-msg"),
            ("project", "hitl-existing"),
            ("emit", "hitl-existing"),
        ]

    @pytest.mark.asyncio
    async def test_agent_hitl_reuses_legacy_pending_request_with_only_continuation_id(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        existing_doc = {
            "request_id": "hitl-legacy",
            "room_id": "room-123",
            "user_message_id": "user-msg-456",
            "source": "agent",
            "prompt": "Need company revenue",
            "prompt_type": HITLPromptType.TEXT.value,
            "choices": None,
            "agent_id": "agent-broker",
            "agent_name": "Cyber Broker Agent",
            "a2a_task_id": "a2a-task-1",
            "a2a_context_id": "a2a-context-1",
            "continuation_message_id": "agent-paused-msg",
            "display_message_id": None,
            "status": HITLStatus.PENDING.value,
            "expires_at": "2026-07-03T00:00:00Z",
            "created_at": "2026-07-02T00:00:00Z",
        }

        async def create_or_reuse_pending_hitl_request(request_data):
            return dict(existing_doc), False

        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        result = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="agent",
            prompt="Need company revenue",
            agent_id="agent-broker",
            agent_name="Cyber Broker Agent",
            a2a_task_id="a2a-task-1",
            a2a_context_id="a2a-context-1",
            continuation_message_id="agent-paused-msg",
            display_message_id="agent-display-msg",
        )

        assert result is not None
        assert result.request_id == "hitl-legacy"
        assert result.display_message_id == "agent-paused-msg"
        mock_hitl_db_service.create_or_reuse_pending_hitl_request.assert_awaited_once()
        create_call = (
            mock_hitl_db_service.create_or_reuse_pending_hitl_request.await_args
        )
        assert create_call.args[0]["display_message_id"] == "agent-display-msg"
        assert create_call.args[0]["continuation_message_id"] == "agent-paused-msg"
        mock_hitl_db_service.create_hitl_request.assert_not_awaited()
        update_calls = mock_hitl_db_service.update_hitl_request.await_args_list
        assert any(
            call.args == ("hitl-legacy",)
            and call.kwargs
            == {
                "prompt": "The agent needs additional information.",
                "prompt_type": HITLPromptType.TEXT.value,
                "choices": None,
            }
            for call in update_calls
        )
        assert any(
            call.args == ("hitl-legacy",)
            and call.kwargs == {"display_message_id": "agent-paused-msg"}
            for call in update_calls
        )
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_awaited_once()
        projection_call = (
            mock_hitl_db_service.persist_pending_hitl_on_agent_message.await_args
        )
        assert projection_call.args == ("agent-paused-msg",)
        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.request_id == "hitl-legacy"
        assert event.message_id == "agent-paused-msg"

    @pytest.mark.asyncio
    async def test_hitl_response_backfills_legacy_continuation_display_before_agent_message_update(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        legacy_doc = {
            "request_id": "hitl-legacy",
            "room_id": "room-123",
            "user_message_id": "user-msg-456",
            "source": "agent",
            "prompt": "Need company revenue",
            "prompt_type": HITLPromptType.TEXT.value,
            "choices": None,
            "agent_id": "agent-broker",
            "agent_name": "Cyber Broker Agent",
            "a2a_task_id": "a2a-task-1",
            "a2a_context_id": "a2a-context-1",
            "continuation_message_id": "agent-paused-msg",
            "display_message_id": None,
            "status": HITLStatus.PENDING.value,
            "expires_at": "2026-07-03T00:00:00Z",
            "created_at": "2026-07-02T00:00:00Z",
        }
        mock_hitl_db_service.get_hitl_request = AsyncMock(return_value=legacy_doc)
        mock_hitl_db_service.claim_hitl_request = AsyncMock(return_value=legacy_doc)
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        hitl_service._handle_agent_response = AsyncMock()

        result = await hitl_service.handle_response(
            room_id="room-123",
            request_id="hitl-legacy",
            user_input="5000000",
            user_id="user-1",
        )

        assert result == {"status": "ok", "request_id": "hitl-legacy"}
        assert any(
            call.kwargs.get("display_message_id") == "agent-paused-msg"
            for call in mock_hitl_db_service.fenced_update_hitl_request.await_args_list
        )
        mock_hitl_db_service.persist_hitl_user_answer.assert_awaited_once_with(
            "agent-paused-msg",
            "5000000",
        )
        mock_hitl_db_service.update_agent_message_task_state.assert_awaited_once_with(
            "agent-paused-msg",
            "completed",
        )

    @pytest.mark.asyncio
    async def test_blocking_followup_hitl_does_not_complete_new_pending_display_message(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        private_followup_prompt = "PRIVATE_SENTINEL_blocking_followup_prompt"
        old_doc = {
            "request_id": "hitl-old",
            "room_id": "room-123",
            "user_message_id": "user-msg-456",
            "source": "agent",
            "prompt": "Need revenue",
            "prompt_type": HITLPromptType.TEXT.value,
            "choices": None,
            "agent_id": "agent-broker",
            "agent_name": "Cyber Broker Agent",
            "a2a_task_id": "a2a-task-1",
            "a2a_context_id": "a2a-context-1",
            "continuation_message_id": "agent-paused-msg",
            "display_message_id": "agent-paused-msg",
            "status": HITLStatus.PENDING.value,
            "expires_at": "2026-07-03T00:00:00Z",
            "created_at": "2026-07-02T00:00:00Z",
        }

        async def create_or_reuse_pending_hitl_request(request_data):
            next_doc = dict(request_data)
            next_doc["request_id"] = "hitl-next"
            return next_doc, True

        agent_reply = MagicMock()
        agent_reply.reply_to_task = AsyncMock(
            return_value={
                "blocking": True,
                "task_state": "input-required",
                "response_text": private_followup_prompt,
            }
        )
        continuation = MagicMock()
        continuation.resume_queue_from_continuation = AsyncMock(return_value=True)

        mock_hitl_db_service.get_hitl_request = AsyncMock(return_value=old_doc)
        mock_hitl_db_service.claim_hitl_request = AsyncMock(return_value=old_doc)
        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        hitl_service._agent_reply = agent_reply
        hitl_service._continuation = continuation

        result = await hitl_service.handle_response(
            room_id="room-123",
            request_id="hitl-old",
            user_input="5000000",
            user_id="user-1",
        )

        assert result == {"status": "ok", "request_id": "hitl-old"}
        mock_hitl_db_service.persist_pending_hitl_on_agent_message.assert_awaited_once()
        projection_call = (
            mock_hitl_db_service.persist_pending_hitl_on_agent_message.await_args
        )
        assert projection_call.args == ("agent-paused-msg",)
        assert projection_call.kwargs["request_id"] == "hitl-next"
        followup_doc = (
            mock_hitl_db_service.create_or_reuse_pending_hitl_request.await_args.args[0]
        )
        assert followup_doc["prompt"] == "The agent needs additional information."
        assert private_followup_prompt not in repr(followup_doc)
        mock_hitl_db_service.persist_hitl_user_answer.assert_not_awaited()
        mock_hitl_db_service.update_agent_message_task_state.assert_not_awaited()
        continuation.resume_queue_from_continuation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blocking_followup_hitl_preserves_v2_run_links(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        old_doc = {
            "request_id": "hitl-old",
            "room_id": "room-123",
            "user_message_id": "user-msg-456",
            "source": "agent",
            "prompt": "Need revenue",
            "prompt_type": HITLPromptType.TEXT.value,
            "choices": None,
            "agent_id": "agent-broker",
            "agent_name": "Cyber Broker Agent",
            "a2a_task_id": "a2a-task-1",
            "a2a_context_id": "a2a-context-1",
            "continuation_message_id": "agent-paused-msg",
            "display_message_id": "agent-paused-msg",
            "orchestration_run_id": "run-msg-1",
            "orchestration_schema_version": 2,
            "status": HITLStatus.PENDING.value,
            "expires_at": "2026-07-03T00:00:00Z",
            "created_at": "2026-07-02T00:00:00Z",
        }

        agent_reply = MagicMock()
        agent_reply.reply_to_task = AsyncMock(
            return_value={
                "blocking": True,
                "task_state": "input-required",
                "response_text": "Need employee count",
            }
        )
        continuation = MagicMock()
        continuation.resume_queue_from_continuation = AsyncMock(return_value=True)

        async def create_or_reuse_pending_hitl_request(request_data):
            next_doc = dict(request_data)
            next_doc["request_id"] = "hitl-next"
            return next_doc, True

        mock_hitl_db_service.get_hitl_request = AsyncMock(return_value=old_doc)
        mock_hitl_db_service.claim_hitl_request = AsyncMock(return_value=old_doc)
        mock_hitl_db_service.create_or_reuse_pending_hitl_request = AsyncMock(
            side_effect=create_or_reuse_pending_hitl_request
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        hitl_service._agent_reply = agent_reply
        hitl_service._continuation = continuation

        await hitl_service.handle_response(
            room_id="room-123",
            request_id="hitl-old",
            user_input="5000000",
            user_id="user-1",
        )

        followup_doc = (
            mock_hitl_db_service.create_or_reuse_pending_hitl_request.await_args.args[0]
        )
        assert followup_doc["orchestration_run_id"] == "run-msg-1"
        assert followup_doc["orchestration_schema_version"] == 2
        mock_hitl_db_service.update_agent_message_task_state.assert_not_awaited()
        continuation.resume_queue_from_continuation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_supervisor_grouped_hitl_allows_multiple_pending_requests_with_same_continuation_id(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        docs = []

        async def create_hitl_request(doc):
            docs.append(doc)
            return True

        mock_hitl_db_service.create_hitl_request = AsyncMock(
            side_effect=create_hitl_request
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        first = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="supervisor",
            prompt="Need revenue",
            continuation_message_id="user-msg-456",
            display_message_id="clarifier-msg-1",
            group_id="clarifier-group",
            group_total=2,
            group_index=0,
        )
        second = await hitl_service.request_input(
            room_id="room-123",
            user_message_id="user-msg-456",
            source="supervisor",
            prompt="Need employee count",
            continuation_message_id="user-msg-456",
            display_message_id="clarifier-msg-2",
            group_id="clarifier-group",
            group_total=2,
            group_index=1,
        )

        assert first is not None
        assert second is not None
        assert mock_hitl_db_service.create_hitl_request.await_count == 2
        mock_hitl_db_service.create_or_reuse_pending_hitl_request.assert_not_awaited()
        assert [doc["source"] for doc in docs] == ["supervisor", "supervisor"]
        assert {doc["continuation_message_id"] for doc in docs} == {"user-msg-456"}
        assert {doc["display_message_id"] for doc in docs} == {
            "clarifier-msg-1",
            "clarifier-msg-2",
        }


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
        hitl_service._persistence = mock_hitl_db_service

        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_pending_hitl_requests.return_value = [request_doc]

        result = await hitl_service.get_pending_requests(sample_hitl_request.room_id)

        assert len(result) == 1
        assert result[0].request_id == sample_hitl_request.request_id

    @pytest.mark.asyncio
    async def test_sanitizes_legacy_agent_requests_but_preserves_supervisor_prompts(
        self, hitl_service, mock_hitl_db_service
    ):
        private_prompt = "PRIVATE_SENTINEL_legacy_agent_hitl_prompt"
        agent_doc = {
            "request_id": "agent-hitl",
            "room_id": "room-123",
            "user_message_id": "user-msg-456",
            "source": "agent",
            "prompt": private_prompt,
            "prompt_type": HITLPromptType.CHOICE.value,
            "choices": [private_prompt],
            "status": HITLStatus.PENDING.value,
        }
        supervisor_doc = {
            "request_id": "supervisor-hitl",
            "room_id": "room-123",
            "user_message_id": "user-msg-456",
            "source": "supervisor",
            "prompt": "Which account should be used?",
            "prompt_type": HITLPromptType.CHOICE.value,
            "choices": ["Personal", "Business"],
            "status": HITLStatus.PENDING.value,
        }
        mock_hitl_db_service.get_pending_hitl_requests.return_value = [
            agent_doc,
            supervisor_doc,
        ]
        hitl_service._persistence = mock_hitl_db_service

        agent_request, supervisor_request = await hitl_service.get_pending_requests(
            "room-123"
        )

        assert agent_request.prompt == "The agent needs additional information."
        assert agent_request.prompt_type == HITLPromptType.TEXT
        assert agent_request.choices is None
        assert private_prompt not in agent_request.model_dump_json()
        assert supervisor_request.prompt == "Which account should be used?"
        assert supervisor_request.prompt_type == HITLPromptType.CHOICE
        assert supervisor_request.choices == ["Personal", "Business"]

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_pending(
        self, hitl_service, mock_hitl_db_service
    ):
        """Should return empty list when no pending requests."""
        hitl_service._persistence = mock_hitl_db_service
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
        hitl_service._persistence = mock_hitl_db_service

        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_pending_hitl_requests_for_message.return_value = [
            request_doc
        ]

        result = await hitl_service.get_pending_requests_for_message("msg-123")

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_sanitizes_legacy_agent_request_for_message(
        self, hitl_service, mock_hitl_db_service
    ):
        private_prompt = "PRIVATE_SENTINEL_message_pending_hitl_prompt"
        mock_hitl_db_service.get_pending_hitl_requests_for_message.return_value = [
            {
                "request_id": "agent-hitl",
                "room_id": "room-123",
                "user_message_id": "user-msg-456",
                "source": "agent",
                "prompt": private_prompt,
                "prompt_type": HITLPromptType.CONFIRMATION.value,
                "choices": [private_prompt],
                "status": HITLStatus.PENDING.value,
            }
        ]
        hitl_service._persistence = mock_hitl_db_service

        result = await hitl_service.get_pending_requests_for_message("user-msg-456")

        assert result[0].prompt == "The agent needs additional information."
        assert result[0].prompt_type == HITLPromptType.TEXT
        assert result[0].choices is None
        assert private_prompt not in result[0].model_dump_json()


# =============================================================================
# Cancel Request Tests
# =============================================================================


class TestCancelRequest:
    """Tests for cancel_request method."""

    @pytest.mark.asyncio
    async def test_cancels_pending_request(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        """Should cancel a pending HITL request."""
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

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
        hitl_service._persistence = mock_hitl_db_service
        mock_hitl_db_service.get_hitl_request.return_value = None

        with pytest.raises(HITLNotFoundError) as exc_info:
            await hitl_service.cancel_request("nonexistent-request")

        assert exc_info.value.message == "HITL request not found"

    @pytest.mark.asyncio
    async def test_raises_403_on_room_mismatch(
        self, hitl_service, mock_hitl_db_service, sample_hitl_request
    ):
        """Should raise 403 when room_id doesn't match."""
        hitl_service._persistence = mock_hitl_db_service

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
        hitl_service._persistence = mock_hitl_db_service

        # Set status to RESPONDED (already resolved)
        sample_hitl_request.status = HITLStatus.RESPONDED
        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = request_doc

        await hitl_service.cancel_request(sample_hitl_request.request_id)

        # Should not call update
        mock_hitl_db_service.update_hitl_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_uses_pending_cas_before_clearing_or_emitting(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
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
        mock_hitl_delivery.emit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_does_not_clear_or_emit_when_pending_cas_loses(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = request_doc
        mock_hitl_db_service.cas_update_hitl_request.return_value = False

        await hitl_service.cancel_request(
            sample_hitl_request.request_id,
            room_id=sample_hitl_request.room_id,
        )

        mock_hitl_db_service.get_and_clear_continuation_on_message.assert_not_awaited()
        mock_hitl_db_service.get_and_clear_continuation_on_user_message.assert_not_awaited()
        mock_hitl_delivery.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_emits_cancel_event(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        """Should emit SSE cancel event."""
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        request_doc = sample_hitl_request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = request_doc

        await hitl_service.cancel_request(
            sample_hitl_request.request_id,
            room_id=sample_hitl_request.room_id,
        )

        mock_hitl_delivery.emit.assert_awaited_once()
        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.event_type == "hitl_resolved"
        assert event.status == HITLStatus.CANCELED.value


class TestCancelRequestsForMessage:
    """Tests for cancel_requests_for_message method."""

    @pytest.mark.asyncio
    async def test_cancels_all_pending_for_message(
        self, hitl_service, mock_hitl_db_service, mock_hitl_delivery
    ):
        """Should cancel all pending requests for a message."""
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

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
        hitl_service._persistence = mock_hitl_db_service
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
        hitl_service._persistence = mock_hitl_db_service
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
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
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

    @pytest.mark.asyncio
    async def test_supervisor_response_missing_continuation_resolves_orchestration_state(
        self,
        hitl_service,
        mock_hitl_db_service,
    ):
        run_store = InMemoryOrchestrationRunStore()
        state = OrchestrationRunState(
            run_id="run-msg-1",
            room_id="room-123",
            user_message_id="user-msg-456",
            goal="Coordinate this",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.AWAITING_USER,
            pending_hitl_request_ids=["hitl-supervisor-1"],
            open_questions=[
                {
                    "request_id": "hitl-supervisor-1",
                    "source": "supervisor",
                    "status": "open",
                    "prompt": "Which account?",
                    "display_message_id": "clarifier-msg-1",
                }
            ],
        )
        await run_store.create_run(state)
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._orchestration_run_store = run_store
        hitl_service._orchestration_recovery_scheduler = MagicMock(
            return_value=MagicMock()
        )
        hitl_service._continuation = MagicMock()
        hitl_service._continuation.resume_queue_from_continuation = AsyncMock(
            return_value=False
        )
        mock_hitl_db_service.get_pending_continuation_on_message.return_value = None
        request = HITLRequest(
            request_id="hitl-supervisor-1",
            room_id="room-123",
            user_message_id="user-msg-456",
            source="supervisor",
            prompt="Which account?",
            continuation_message_id="user-msg-456",
            display_message_id="clarifier-msg-1",
            orchestration_run_id="run-msg-1",
            orchestration_schema_version=2,
            status=HITLStatus.PENDING,
        )

        await hitl_service._handle_supervisor_response(
            request,
            "Use the enterprise account",
        )

        persisted = await run_store.get_run("run-msg-1")
        assert persisted is not None
        assert persisted.status == OrchestrationStatus.RUNNING
        assert persisted.pending_hitl_request_ids == []
        assert persisted.open_questions[0]["status"] == "resolved"
        assert persisted.open_questions[0]["answer"] == "Use the enterprise account"
        assert persisted.facts[0]["source"] == "hitl_user_reply"
        hitl_service._continuation.resume_queue_from_continuation.assert_not_awaited()
        hitl_service._orchestration_recovery_scheduler.assert_called_once()
        recovery_call = hitl_service._orchestration_recovery_scheduler.call_args
        assert recovery_call.args[0].room_user_message_id == "user-msg-456"
        assert recovery_call.args[0].is_recovery is True
        assert recovery_call.kwargs == {
            "reason": "hitl_continuation_lost",
        }

    @pytest.mark.asyncio
    async def test_missing_continuation_keeps_response_retryable_when_recovery_unscheduled(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
    ):
        run_store = InMemoryOrchestrationRunStore()
        state = OrchestrationRunState(
            run_id="run-msg-1",
            room_id="room-123",
            user_message_id="user-msg-456",
            goal="Coordinate this",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.AWAITING_USER,
            pending_hitl_request_ids=["hitl-supervisor-1"],
            open_questions=[
                {
                    "request_id": "hitl-supervisor-1",
                    "source": "supervisor",
                    "status": "open",
                    "prompt": "Which account?",
                }
            ],
        )
        await run_store.create_run(state)
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
        hitl_service._orchestration_run_store = run_store
        mock_hitl_db_service.get_pending_continuation_on_message.return_value = None
        request = HITLRequest(
            request_id="hitl-supervisor-1",
            room_id="room-123",
            user_message_id="user-msg-456",
            source="supervisor",
            prompt="Which account?",
            continuation_message_id="user-msg-456",
            orchestration_run_id="run-msg-1",
            orchestration_schema_version=2,
            status=HITLStatus.PENDING,
        )
        request_doc = request.model_dump(mode="json")
        mock_hitl_db_service.get_hitl_request.return_value = request_doc
        mock_hitl_db_service.claim_hitl_request.return_value = request_doc

        with pytest.raises(HITLRoutingFailedError):
            await hitl_service.handle_response(
                room_id=request.room_id,
                request_id=request.request_id,
                user_input="Account A",
                user_id="user-1",
            )

        retry_updates = [
            call.args[2]
            for call in mock_hitl_db_service.fenced_update_hitl_request.await_args_list
            if len(call.args) >= 3 and isinstance(call.args[2], dict)
        ]
        assert any(
            update.get("status") == HITLStatus.PENDING.value
            and update.get("claim_id") is None
            for update in retry_updates
        )
        assert not any(
            update.get("status") == HITLStatus.CANCELED.value
            for update in retry_updates
        )
        persisted = await run_store.get_run("run-msg-1")
        assert persisted is not None
        assert persisted.status == OrchestrationStatus.RUNNING
        assert persisted.open_questions[0]["answer"] == "Account A"
        mock_hitl_delivery.emit.assert_not_awaited()

        hitl_service._orchestration_recovery_scheduler = MagicMock(
            return_value=MagicMock()
        )
        result = await hitl_service.handle_response(
            room_id=request.room_id,
            request_id=request.request_id,
            user_input="Account A",
            user_id="user-1",
        )

        assert result == {"status": "ok", "request_id": request.request_id}
        persisted_after_retry = await run_store.get_run("run-msg-1")
        assert persisted_after_retry is not None
        assert len(persisted_after_retry.open_questions) == 1
        assert len(persisted_after_retry.facts) == 1
        hitl_service._orchestration_recovery_scheduler.assert_called_once()

    @pytest.mark.asyncio
    async def test_group_recovery_resolves_all_supervisor_siblings(
        self,
        hitl_service,
        mock_hitl_db_service,
    ):
        run_store = InMemoryOrchestrationRunStore()
        state = OrchestrationRunState(
            run_id="run-msg-1",
            room_id="room-123",
            user_message_id="user-msg-456",
            goal="Coordinate this",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.AWAITING_USER,
            pending_hitl_request_ids=["hitl-1", "hitl-2"],
            open_questions=[
                {
                    "request_id": "hitl-1",
                    "source": "supervisor",
                    "status": "open",
                    "prompt": "First?",
                },
                {
                    "request_id": "hitl-2",
                    "source": "supervisor",
                    "status": "open",
                    "prompt": "Second?",
                },
            ],
        )
        await run_store.create_run(state)
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._orchestration_run_store = run_store
        mock_hitl_db_service.get_hitl_group_requests.return_value = [
            {
                "request_id": "hitl-1",
                "room_id": "room-123",
                "source": "supervisor",
            },
            {
                "request_id": "hitl-2",
                "room_id": "room-123",
                "source": "supervisor",
            },
        ]
        request = HITLRequest(
            request_id="hitl-2",
            room_id="room-123",
            user_message_id="user-msg-456",
            source="supervisor",
            prompt="Second?",
            continuation_message_id="user-msg-456",
            orchestration_run_id="run-msg-1",
            orchestration_schema_version=2,
            group_id="group-1",
            group_total=2,
            group_index=1,
            status=HITLStatus.PENDING,
        )

        recorded = await hitl_service._record_orchestration_supervisor_response(
            request,
            "Q: First?\nA: one\n\nQ: Second?\nA: two",
        )

        assert recorded is True
        persisted = await run_store.get_run("run-msg-1")
        assert persisted is not None
        assert persisted.status == OrchestrationStatus.RUNNING
        assert persisted.pending_hitl_request_ids == []
        assert {question["status"] for question in persisted.open_questions} == {
            "resolved"
        }
        assert [
            question["request_id"] for question in persisted.open_questions
        ] == ["hitl-1", "hitl-2"]
        assert persisted.facts[0]["request_ids"] == ["hitl-2", "hitl-1"]

    @pytest.mark.asyncio
    async def test_recovery_retains_creating_and_cleanup_failed_pending_refs(
        self,
        hitl_service,
        mock_hitl_db_service,
    ):
        run_store = InMemoryOrchestrationRunStore()
        state = OrchestrationRunState(
            run_id="run-msg-1",
            room_id="room-123",
            user_message_id="user-msg-456",
            goal="Coordinate this",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.AWAITING_USER,
            pending_hitl_request_ids=["hitl-1", "hitl-2", "hitl-3"],
            open_questions=[
                {
                    "request_id": "hitl-1",
                    "source": "supervisor",
                    "status": "open",
                },
                {
                    "request_id": "hitl-2",
                    "source": "supervisor",
                    "status": "creating",
                },
                {
                    "request_id": "hitl-3",
                    "source": "supervisor",
                    "status": "cleanup_failed",
                },
            ],
        )
        await run_store.create_run(state)
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._orchestration_run_store = run_store
        request = HITLRequest(
            request_id="hitl-1",
            room_id="room-123",
            user_message_id="user-msg-456",
            source="supervisor",
            prompt="First?",
            continuation_message_id="user-msg-456",
            orchestration_run_id="run-msg-1",
            orchestration_schema_version=2,
            status=HITLStatus.PENDING,
        )

        recorded = await hitl_service._record_orchestration_supervisor_response(
            request,
            "one",
        )

        assert recorded is True
        persisted = await run_store.get_run("run-msg-1")
        assert persisted is not None
        assert persisted.status == OrchestrationStatus.AWAITING_USER
        assert persisted.pending_hitl_request_ids == ["hitl-2", "hitl-3"]


class TestGroupedHandleResponse:
    """Tests for grouped HITL response routing."""

    @pytest.mark.asyncio
    async def test_first_group_answer_waits_for_remaining_answers(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
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
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
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
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
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
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery
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
        hitl_service._persistence = mock_hitl_db_service
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
        hitl_service._persistence = mock_hitl_db_service
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
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        """Should emit correct data for INPUT_REQUESTED event."""
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        await hitl_service._emit_hitl_event(
            room_id=sample_hitl_request.room_id,
            event_type=HITLEventType.INPUT_REQUESTED,
            request=sample_hitl_request,
        )

        mock_hitl_delivery.emit.assert_awaited_once()
        event = mock_hitl_delivery.emit.await_args.args[0]

        assert event.room_id == sample_hitl_request.room_id
        assert event.event_type == "hitl_request"
        assert event.request_id == sample_hitl_request.request_id
        assert event.prompt == sample_hitl_request.prompt
        assert event.source == sample_hitl_request.source

    @pytest.mark.asyncio
    async def test_emits_status_update_event(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        """Should emit correct data for status update events."""
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        await hitl_service._emit_hitl_event(
            room_id=sample_hitl_request.room_id,
            event_type=HITLEventType.INPUT_RECEIVED,
            request=sample_hitl_request,
        )

        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.event_type == "hitl_resolved"
        assert event.status == HITLStatus.RESPONDED.value

    @pytest.mark.asyncio
    async def test_includes_error_message_on_error_event(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        """Should include error message for ERROR events."""
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        await hitl_service._emit_hitl_event(
            room_id=sample_hitl_request.room_id,
            event_type=HITLEventType.ERROR,
            request=sample_hitl_request,
            error="Something went wrong",
        )

        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.error_message == "Something went wrong"

    @pytest.mark.asyncio
    async def test_resolves_client_request_id_from_message_id_when_user_row_missing(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        """SSE payload should include client_request_id via DB resolver on message_id."""
        mock_hitl_db_service.get_room_user_message_by_message_id = AsyncMock(
            return_value=None
        )
        mock_hitl_db_service.resolve_client_request_id_for_message_id = AsyncMock(
            return_value="cr-resolved-via-message-id"
        )
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        req = sample_hitl_request.model_copy(
            update={"display_message_id": "test-agent-msg-001"}
        )

        await hitl_service._emit_hitl_event(
            room_id=req.room_id,
            event_type=HITLEventType.INPUT_REQUESTED,
            request=req,
        )

        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.message_id == "test-agent-msg-001"
        assert event.client_request_id == "cr-resolved-via-message-id"
        mock_hitl_db_service.resolve_client_request_id_for_message_id.assert_called_once_with(
            "test-agent-msg-001"
        )

    @pytest.mark.asyncio
    async def test_prefers_user_message_client_request_id_over_resolver(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
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
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        req = sample_hitl_request.model_copy(
            update={"display_message_id": "test-agent-msg-001"}
        )

        await hitl_service._emit_hitl_event(
            room_id=req.room_id,
            event_type=HITLEventType.INPUT_REQUESTED,
            request=req,
        )

        event = mock_hitl_delivery.emit.await_args.args[0]
        assert event.client_request_id == "cr-from-user-row"
        mock_hitl_db_service.resolve_client_request_id_for_message_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_emitted_hitl_events_include_related_message_id(
        self,
        hitl_service,
        mock_hitl_db_service,
        mock_hitl_delivery,
        sample_hitl_request,
    ):
        """HITL events should include related_message_id for frontend resume correlation."""
        hitl_service._persistence = mock_hitl_db_service
        hitl_service._delivery = mock_hitl_delivery

        await hitl_service._emit_hitl_event(
            room_id=sample_hitl_request.room_id,
            event_type=HITLEventType.INPUT_REQUESTED,
            request=sample_hitl_request,
        )
        request_event = mock_hitl_delivery.emit.await_args.args[0]
        assert request_event.related_message_id == sample_hitl_request.user_message_id

        await hitl_service._emit_hitl_event(
            room_id=sample_hitl_request.room_id,
            event_type=HITLEventType.INPUT_RECEIVED,
            request=sample_hitl_request,
        )
        response_event = mock_hitl_delivery.emit.await_args.args[0]
        assert response_event.related_message_id == sample_hitl_request.user_message_id


class TestRecoverStaleProcessing:
    @pytest.mark.asyncio
    async def test_finalizes_stale_processing_agent_hitl_when_followup_pending_exists(
        self, hitl_service, mock_hitl_db_service
    ):
        stale_doc = {
            "request_id": "hitl-old",
            "room_id": "room-123",
            "user_message_id": "user-msg-456",
            "source": "agent",
            "status": HITLStatus.PROCESSING.value,
            "claim_id": "claim-old",
            "routing_completed_at": None,
            "continuation_message_id": "agent-paused-msg",
            "display_message_id": "agent-paused-msg",
            "agent_id": "agent-broker",
            "a2a_task_id": "a2a-task-1",
            "a2a_context_id": "a2a-context-1",
        }
        mock_hitl_db_service.iter_stale_processing_hitl_requests = MagicMock(
            return_value=_iter_docs([stale_doc])
        )
        mock_hitl_db_service.find_pending_hitl_request_for_agent_message = AsyncMock(
            return_value={
                "request_id": "hitl-next",
                "room_id": "room-123",
                "source": "agent",
                "status": HITLStatus.PENDING.value,
                "continuation_message_id": "agent-paused-msg",
                "display_message_id": "agent-paused-msg",
            }
        )
        hitl_service._persistence = mock_hitl_db_service

        recovered = await hitl_service.recover_stale_processing()

        assert recovered == 1
        mock_hitl_db_service.find_pending_hitl_request_for_agent_message.assert_awaited_once_with(
            room_id="room-123",
            display_message_id="agent-paused-msg",
            continuation_message_id="agent-paused-msg",
            agent_id="agent-broker",
            a2a_task_id="a2a-task-1",
            a2a_context_id="a2a-context-1",
        )
        mock_hitl_db_service.cas_update_hitl_request.assert_awaited_once_with(
            "hitl-old",
            expected_status=HITLStatus.PROCESSING.value,
            status=HITLStatus.RESPONDED.value,
            routing_completed_at=ANY,
        )
        mock_hitl_db_service.release_hitl_group_routing.assert_not_awaited()

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
        mock_hitl_db_service.iter_stale_processing_hitl_requests = MagicMock(
            return_value=_iter_docs([stale_doc])
        )
        hitl_service._persistence = mock_hitl_db_service

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
