# tests/test_hitl_turn_events.py
"""
Integration tests that drive real HITLService methods and verify the injected
turn_event_appender is called with correct event types and payloads.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from services.hitl_service import HITLService
from models.hitl import HITLPromptType, HITLStatus

_NOW = datetime(2026, 4, 12, tzinfo=timezone.utc)


@pytest.fixture
def mock_appender():
    """Mock TurnEventAppender — injected into HITLService._turn_appender."""
    appender = MagicMock()
    appender.append = AsyncMock(return_value=MagicMock())
    return appender


@pytest.fixture
def hitl_service(mock_appender):
    """HITLService wired with mock dependencies."""
    svc = HITLService()

    # Mock database_service — returns success for persistence calls
    mock_db = MagicMock()
    mock_db.count_hitl_requests_for_message = AsyncMock(return_value=0)
    mock_db.create_hitl_request = AsyncMock(return_value=True)
    mock_db.update_agent_message_task_state = AsyncMock()
    mock_db.persist_hitl_user_answer = AsyncMock()
    mock_db.persist_hitl_group_metadata = AsyncMock()
    mock_db.get_hitl_request = AsyncMock()
    mock_db.claim_hitl_request = AsyncMock()
    mock_db.update_hitl_request = AsyncMock()
    mock_db.fenced_update_hitl_request = AsyncMock(return_value=True)
    mock_db.get_and_clear_continuation_on_message = AsyncMock()
    mock_db.get_and_clear_continuation_on_user_message = AsyncMock()
    mock_db.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
    mock_db.reset_last_notified_state = AsyncMock()
    mock_db.count_pending_in_hitl_group = AsyncMock(return_value=0)

    # Mock SSE manager — silent no-op
    mock_sse = MagicMock()
    mock_sse.send_hitl_event = AsyncMock()
    mock_sse.broadcast_to_room = AsyncMock()

    svc._db_service = mock_db
    svc._sse_manager = mock_sse
    svc._turn_appender = mock_appender
    return svc


class TestHitlRequestedTurnEvent:
    @pytest.mark.asyncio
    async def test_request_input_emits_hitl_requested(self, hitl_service, mock_appender):
        """Drive HITLService.request_input() and verify hitl_requested event."""
        result = await hitl_service.request_input(
            room_id="room_1",
            user_message_id="turn_1",
            source="agent",
            agent_name="Agent A",
            prompt="Which option?",
            prompt_type=HITLPromptType.CHOICE,
            choices=["A", "B"],
        )

        assert result is not None  # request was created
        mock_appender.append.assert_called_once()
        call_args = mock_appender.append.call_args
        assert call_args.args[0] == "room_1"         # room_id
        assert call_args.args[1] == "turn_1"          # turn_id
        assert call_args.args[2] == "hitl_requested"  # event_type
        payload = call_args.args[3]
        assert payload["source"] == "agent"
        assert payload["agent_name"] == "Agent A"
        assert payload["prompt"] == "Which option?"
        assert payload["choices"] == ["A", "B"]

    @pytest.mark.asyncio
    async def test_request_input_supervisor_source(self, hitl_service, mock_appender):
        """Supervisor-sourced HITL emits hitl_requested with source=supervisor."""
        await hitl_service.request_input(
            room_id="room_1",
            user_message_id="turn_1",
            source="supervisor",
            prompt="Proceed?",
            prompt_type=HITLPromptType.CONFIRMATION,
        )

        payload = mock_appender.append.call_args.args[3]
        assert payload["source"] == "supervisor"
        assert payload["prompt_type"] == "confirmation"

    @pytest.mark.asyncio
    async def test_request_input_with_group_metadata(self, hitl_service, mock_appender):
        """Grouped HITL request includes group_id, group_total, group_index."""
        await hitl_service.request_input(
            room_id="room_1",
            user_message_id="turn_1",
            source="supervisor",
            prompt="Q1?",
            prompt_type=HITLPromptType.TEXT,
            group_id="grp_1",
            group_total=3,
            group_index=0,
        )

        payload = mock_appender.append.call_args.args[3]
        assert payload["group_id"] == "grp_1"
        assert payload["group_total"] == 3
        assert payload["group_index"] == 0


class TestHitlAnsweredTurnEvent:
    @pytest.mark.asyncio
    async def test_handle_response_emits_hitl_answered(self, hitl_service, mock_appender):
        """Drive HITLService.handle_response() and verify hitl_answered event."""
        # Pre-seed a claimed request doc that handle_response will load
        claimed_doc = {
            "request_id": "hitl_abc",
            "room_id": "room_1",
            "user_message_id": "turn_1",
            "source": "agent",
            "prompt": "Which option?",
            "prompt_type": "text",
            "status": "processing",
            "agent_id": "agent_1",
            "agent_name": "Agent A",
            "a2a_task_id": "task_1",
            "a2a_context_id": None,
            "continuation_message_id": "cont_1",
            "display_message_id": "disp_1",
            "source_step_id": None,
            "choices": None,
            "group_id": None,
            "group_total": None,
            "group_index": None,
            "user_input": "Option A",
            "responded_at": None,
            "responded_by_user_id": None,
            "expires_at": None,
            "created_at": _NOW,
            "claim_id": "claim_1",
        }
        hitl_service._db_service.claim_hitl_request = AsyncMock(return_value=claimed_doc)

        # Mock the A2A routing to succeed (source=agent uses reply_to_task)
        mock_a2a = MagicMock()
        mock_a2a.reply_to_task = AsyncMock(return_value={
            "blocking": False,
        })
        hitl_service._a2a_service = mock_a2a

        await hitl_service.handle_response(
            room_id="room_1",
            request_id="hitl_abc",
            user_input="Option A",
            user_id="user_1",
        )

        # Find the hitl_answered call among appender calls
        answered_calls = [
            c for c in mock_appender.append.call_args_list
            if c.args[2] == "hitl_answered"
        ]
        assert len(answered_calls) == 1
        payload = answered_calls[0].args[3]
        assert payload["hitl_id"] == "hitl_abc"
        assert payload["answer"] == "Option A"


class TestHitlTerminalTurnEvents:
    @pytest.mark.asyncio
    async def test_cancel_request_emits_hitl_canceled(self, hitl_service, mock_appender):
        """Drive HITLService.cancel_request() and verify hitl_canceled event."""
        # Pre-seed a pending request doc
        pending_doc = {
            "request_id": "hitl_abc",
            "room_id": "room_1",
            "user_message_id": "turn_1",
            "source": "agent",
            "prompt": "Which option?",
            "prompt_type": "text",
            "status": "pending",
            "agent_id": None,
            "agent_name": None,
            "a2a_task_id": None,
            "a2a_context_id": None,
            "continuation_message_id": "cont_1",
            "display_message_id": None,
            "source_step_id": None,
            "choices": None,
            "group_id": None,
            "group_total": None,
            "group_index": None,
            "user_input": None,
            "responded_at": None,
            "responded_by_user_id": None,
            "expires_at": None,
            "created_at": _NOW,
            "claim_id": None,
        }
        hitl_service._db_service.get_hitl_request = AsyncMock(return_value=pending_doc)

        await hitl_service.cancel_request(request_id="hitl_abc", room_id="room_1")

        canceled_calls = [
            c for c in mock_appender.append.call_args_list
            if c.args[2] == "hitl_canceled"
        ]
        assert len(canceled_calls) == 1
        assert canceled_calls[0].args[3]["hitl_id"] == "hitl_abc"

    @pytest.mark.asyncio
    async def test_hitl_error_emitted_on_routing_failure(self, hitl_service, mock_appender):
        """Drive handle_response with a routing exception -> hitl_error event."""
        claimed_doc = {
            "request_id": "hitl_abc",
            "room_id": "room_1",
            "user_message_id": "turn_1",
            "source": "agent",
            "prompt": "Pick one",
            "prompt_type": "text",
            "status": "processing",
            "agent_id": "agent_1",
            "agent_name": "Agent A",
            "a2a_task_id": "task_1",
            "a2a_context_id": None,
            "continuation_message_id": "cont_1",
            "display_message_id": "disp_1",
            "source_step_id": None,
            "choices": None,
            "group_id": None,
            "group_total": None,
            "group_index": None,
            "user_input": "yes",
            "responded_at": None,
            "responded_by_user_id": None,
            "expires_at": None,
            "created_at": _NOW,
            "claim_id": "claim_1",
        }
        hitl_service._db_service.claim_hitl_request = AsyncMock(return_value=claimed_doc)

        # Make A2A routing fail
        mock_a2a = MagicMock()
        mock_a2a.reply_to_task = AsyncMock(
            side_effect=RuntimeError("Routing failed")
        )
        hitl_service._a2a_service = mock_a2a

        # handle_response raises HTTPException(502) after emitting hitl_error
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await hitl_service.handle_response(
                room_id="room_1",
                request_id="hitl_abc",
                user_input="yes",
                user_id="user_1",
            )
        assert exc_info.value.status_code == 502

        error_calls = [
            c for c in mock_appender.append.call_args_list
            if c.args[2] == "hitl_error"
        ]
        assert len(error_calls) == 1
        assert "Routing failed" in error_calls[0].args[3]["error"]
