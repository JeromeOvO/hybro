# tests/test_phase_1c_integration.py
"""
Integration tests that drive real orchestrator/service methods and verify
the injected turn_event_appender receives the expected event sequences.

These tests call production code — NOT the mock.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from models.hitl import HITLPromptType, HITLStatus

_NOW = datetime(2026, 4, 12, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_appender():
    appender = MagicMock()
    appender.append = AsyncMock(return_value=MagicMock())
    return appender


@pytest.fixture
def supervisor_with_appender(mock_appender):
    """SupervisorExecutor with real _emit_phase() wired to mock appender."""
    from modules.SupervisorExecutor import SupervisorExecutor

    executor = SupervisorExecutor.__new__(SupervisorExecutor)
    executor._turn_appender = mock_appender
    executor.sse_manager = MagicMock(send_processing_status=AsyncMock())
    executor.database_service = MagicMock()
    executor.room_services = MagicMock()
    return executor


@pytest.fixture
def hitl_service_with_appender(mock_appender):
    """HITLService with real methods wired to mock appender + mock DB/SSE."""
    from services.hitl_service import HITLService

    svc = HITLService()
    mock_db = MagicMock()
    mock_db.count_hitl_requests_for_message = AsyncMock(return_value=0)
    mock_db.create_hitl_request = AsyncMock(return_value=True)
    mock_db.update_agent_message_task_state = AsyncMock()
    mock_db.persist_hitl_user_answer = AsyncMock()
    mock_db.persist_hitl_group_metadata = AsyncMock()
    mock_db.get_hitl_request = AsyncMock()
    mock_db.update_hitl_request = AsyncMock()
    mock_db.fenced_update_hitl_request = AsyncMock(return_value=True)
    mock_db.get_and_clear_continuation_on_message = AsyncMock()
    mock_db.get_and_clear_continuation_on_user_message = AsyncMock()
    mock_db.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
    mock_db.reset_last_notified_state = AsyncMock()
    mock_db.count_pending_in_hitl_group = AsyncMock(return_value=0)
    svc._db_service = mock_db
    svc._sse_manager = MagicMock(send_hitl_event=AsyncMock(), broadcast_to_room=AsyncMock())
    svc._turn_appender = mock_appender
    return svc


# ---------------------------------------------------------------------------
# Supervisor phase sequence — via real _emit_phase()
# ---------------------------------------------------------------------------

class TestSupervisorPhaseSequence:
    """Drive SupervisorExecutor._emit_phase() through the expected phase order."""

    @pytest.mark.asyncio
    async def test_supervisor_normal_flow_phases(self, supervisor_with_appender, mock_appender):
        """Planning -> Delegating -> Evaluating -> Synthesizing sequence."""
        executor = supervisor_with_appender

        await executor._emit_phase("room_1", "turn_1", {"name": "planning"})
        await executor._emit_phase("room_1", "turn_1",
            {"name": "delegating", "agent_names": ["Agent A", "Agent B"], "count": 2})
        await executor._emit_phase("room_1", "turn_1", {"name": "evaluating"})
        await executor._emit_phase("room_1", "turn_1", {"name": "synthesizing"})

        assert mock_appender.append.call_count == 4
        emitted_names = [
            call.args[3]["phase"]["name"]
            for call in mock_appender.append.call_args_list
        ]
        assert emitted_names == ["planning", "delegating", "evaluating", "synthesizing"]

    @pytest.mark.asyncio
    async def test_supervisor_with_hitl_pause_and_resume(
        self, supervisor_with_appender, hitl_service_with_appender, mock_appender,
    ):
        """Planning -> Delegating -> (HITL requested) -> awaiting_input -> (resume) -> Evaluating."""
        executor = supervisor_with_appender
        hitl_svc = hitl_service_with_appender

        await executor._emit_phase("room_1", "turn_1", {"name": "planning"})
        await executor._emit_phase("room_1", "turn_1",
            {"name": "delegating", "agent_names": ["A"], "count": 1})

        # HITL request via real HITLService.request_input()
        await hitl_svc.request_input(
            room_id="room_1", user_message_id="turn_1",
            source="agent", agent_name="A",
            prompt="?", prompt_type=HITLPromptType.TEXT,
        )

        await executor._emit_phase("room_1", "turn_1", {"name": "awaiting_input"})
        await executor._emit_phase("room_1", "turn_1", {"name": "evaluating"})

        # Extract event types from appender calls
        types = [call.args[2] for call in mock_appender.append.call_args_list]
        assert "phase_changed" in types
        assert "hitl_requested" in types
        # Phase sequence order
        phase_names = [
            call.args[3]["phase"]["name"]
            for call in mock_appender.append.call_args_list
            if call.args[2] == "phase_changed"
        ]
        assert phase_names == ["planning", "delegating", "awaiting_input", "evaluating"]


# ---------------------------------------------------------------------------
# Debate round phases — via real _emit_phase()
# ---------------------------------------------------------------------------

class TestDebateRoundPhases:
    @pytest.mark.asyncio
    async def test_debate_emits_round_per_agent(self, supervisor_with_appender, mock_appender):
        """3-agent debate produces 3 round phase events via _emit_phase."""
        executor = supervisor_with_appender

        for i in range(3):
            await executor._emit_phase("room_1", "turn_1",
                {"name": "round", "current": i + 1, "total": 3})

        assert mock_appender.append.call_count == 3
        rounds = [
            call.args[3]["phase"]["current"]
            for call in mock_appender.append.call_args_list
        ]
        assert rounds == [1, 2, 3]


# ---------------------------------------------------------------------------
# Workflow step phases — via real WorkflowCenter._emit_workflow_phase()
# ---------------------------------------------------------------------------

class TestWorkflowStepPhases:
    @pytest.mark.asyncio
    async def test_workflow_emits_step_per_task(self, mock_appender):
        """WorkflowCenter._emit_workflow_phase() emits step-per-task events."""
        from modules.WorkflowCenter import WorkflowCenter

        wc = WorkflowCenter.__new__(WorkflowCenter)
        wc._turn_appender = mock_appender
        wc.sse_manager = MagicMock()

        steps = ["Data Collection", "Analysis", "Report"]
        for i, name in enumerate(steps):
            await wc._emit_workflow_phase("room_1", "turn_1", {
                "name": "workflow_step",
                "current": i + 1,
                "total": len(steps),
                "step_name": name,
            })

        assert mock_appender.append.call_count == 3
        step_names = [
            call.args[3]["phase"]["step_name"]
            for call in mock_appender.append.call_args_list
        ]
        assert step_names == ["Data Collection", "Analysis", "Report"]


# ---------------------------------------------------------------------------
# HITL lifecycle — via real HITLService methods
# ---------------------------------------------------------------------------

class TestHitlTurnEventIntegration:
    @pytest.mark.asyncio
    async def test_hitl_full_lifecycle_happy_path(self, hitl_service_with_appender, mock_appender):
        """request_input() -> handle_response() produces hitl_requested -> hitl_answered."""
        svc = hitl_service_with_appender

        result = await svc.request_input(
            room_id="room_1", user_message_id="turn_1",
            source="agent", agent_name="A",
            prompt="Choose", prompt_type=HITLPromptType.CHOICE,
            choices=["X", "Y"],
        )
        assert result is not None

        # Set up claimed doc for handle_response
        claimed = {
            "request_id": result.request_id, "room_id": "room_1",
            "user_message_id": "turn_1", "source": "agent",
            "prompt": "Choose", "prompt_type": "choice",
            "status": "processing", "agent_id": "agent_1",
            "agent_name": "A", "a2a_task_id": "task_1",
            "a2a_context_id": None, "continuation_message_id": "cont_1",
            "display_message_id": "disp_1", "source_step_id": None,
            "choices": ["X", "Y"], "group_id": None, "group_total": None,
            "group_index": None, "user_input": "X", "responded_at": None,
            "responded_by_user_id": None, "expires_at": None,
            "created_at": _NOW, "claim_id": "claim_1",
        }
        svc._db_service.claim_hitl_request = AsyncMock(return_value=claimed)
        svc._a2a_service = MagicMock(reply_to_task=AsyncMock(return_value={
            "blocking": False,
        }))

        await svc.handle_response(
            room_id="room_1", request_id=result.request_id,
            user_input="X", user_id="user_1",
        )

        types = [call.args[2] for call in mock_appender.append.call_args_list]
        assert "hitl_requested" in types
        assert "hitl_answered" in types

    @pytest.mark.asyncio
    async def test_hitl_canceled_lifecycle(self, hitl_service_with_appender, mock_appender):
        """request_input() -> cancel_request() produces hitl_requested -> hitl_canceled."""
        svc = hitl_service_with_appender

        result = await svc.request_input(
            room_id="room_1", user_message_id="turn_1",
            source="supervisor", prompt="?",
            prompt_type=HITLPromptType.TEXT,
            continuation_message_id="cont_1",
        )
        assert result is not None

        # Set up pending doc for cancel
        pending = {
            "request_id": result.request_id, "room_id": "room_1",
            "user_message_id": "turn_1", "source": "supervisor",
            "prompt": "?", "prompt_type": "text", "status": "pending",
            "agent_id": None, "agent_name": None, "a2a_task_id": None,
            "a2a_context_id": None, "continuation_message_id": "cont_1",
            "display_message_id": None, "source_step_id": None,
            "choices": None, "group_id": None, "group_total": None,
            "group_index": None, "user_input": None, "responded_at": None,
            "responded_by_user_id": None, "expires_at": None,
            "created_at": _NOW, "claim_id": None,
        }
        svc._db_service.get_hitl_request = AsyncMock(return_value=pending)

        await svc.cancel_request(request_id=result.request_id, room_id="room_1")

        types = [call.args[2] for call in mock_appender.append.call_args_list]
        assert "hitl_requested" in types
        assert "hitl_canceled" in types
