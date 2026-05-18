"""
Unit tests for SupervisorExecutor module.

Tests cover:
- _log_and_return: passes through result, includes trajectory metadata
- _checkpoint_trajectory: persists trajectory snapshot, handles missing message
- _save_interrupted_state: saves trajectory on unexpected failure
- CLARIFY cleanup compensation: orphan requests are canceled on failure
"""

import ast
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from modules.SupervisorExecutor import SupervisorExecutor
from models.supervisor_v2 import (
    ActionType,
    AgentProfile,
    ClarifyQuestion,
    DelegateTarget,
    RoomConfig,
    RunStatus,
    StepStatus,
    SupervisorAction,
    SupervisorRunResult,
    SupervisorTrajectory,
    V2StepResult,
)

_ROOT = Path(__file__).resolve().parents[1]


def _make_supervisor_executor():
    se = object.__new__(SupervisorExecutor)
    se.database_service = AsyncMock()
    se.sse_manager = MagicMock()
    se.room_services = MagicMock()
    se.supervisor_service = MagicMock()
    se.tsm = MagicMock()
    se.agent_dispatcher = MagicMock()
    se.agent_message_processor = MagicMock()
    se.room_memory_service = MagicMock()
    se.rate_limit_service = MagicMock()
    se.room_coordinator_service = MagicMock()
    se.hitl_coordinator = MagicMock()
    return se


def test_dispatch_targets_cancelled_error_handler_reraises():
    tree = ast.parse(
        (_ROOT / "execution" / "orchestration" / "supervisor_executor.py").read_text()
    )
    dispatch_targets = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_dispatch_targets"
    )
    handlers = [
        node
        for node in ast.walk(dispatch_targets)
        if isinstance(node, ast.ExceptHandler)
        and ast.unparse(node.type) == "asyncio.CancelledError"
    ]
    assert handlers, "CancelledError handler not found"
    for handler in handlers:
        assert any(isinstance(stmt, ast.Raise) for stmt in handler.body)
        assert not any(isinstance(stmt, ast.Return) for stmt in handler.body)


# =============================================================================
# _log_and_return Tests
# =============================================================================


class TestLogAndReturn:
    def test_returns_result_unchanged(self):
        trajectory = SupervisorTrajectory()
        result = SupervisorRunResult(status="completed", trajectory=trajectory)

        returned = SupervisorExecutor._log_and_return(
            "room-1", trajectory, result
        )
        assert returned is result
        assert returned.status == "completed"

    def test_returns_result_in_debate_mode(self):
        trajectory = SupervisorTrajectory()
        result = SupervisorRunResult(status="completed", trajectory=trajectory)

        returned = SupervisorExecutor._log_and_return(
            "room-1", trajectory, result, debate_mode=True
        )
        assert returned is result


# =============================================================================
# _checkpoint_trajectory Tests
# =============================================================================


class TestCheckpointTrajectory:
    @pytest.mark.asyncio
    async def test_persists_trajectory_to_user_message(self):
        se = _make_supervisor_executor()
        user_message = MagicMock()
        user_message.extend_info = {}
        se.database_service.get_room_user_message_by_message_id.return_value = (
            user_message
        )
        se.database_service.update_room_user_message_by_message_id.return_value = True

        trajectory = SupervisorTrajectory()
        result = await se._checkpoint_trajectory("msg-1", trajectory)

        assert result is user_message
        se.database_service.update_room_user_message_by_message_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_message_not_found(self):
        se = _make_supervisor_executor()
        se.database_service.get_room_user_message_by_message_id.return_value = None

        trajectory = SupervisorTrajectory()
        result = await se._checkpoint_trajectory("msg-missing", trajectory)

        assert result is None

    @pytest.mark.asyncio
    async def test_uses_cached_message(self):
        se = _make_supervisor_executor()
        cached = MagicMock()
        cached.extend_info = {}
        se.database_service.update_room_user_message_by_message_id.return_value = True

        trajectory = SupervisorTrajectory()
        result = await se._checkpoint_trajectory("msg-1", trajectory, cached)

        assert result is cached
        se.database_service.get_room_user_message_by_message_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_initializes_extend_info_if_not_dict(self):
        se = _make_supervisor_executor()
        user_message = MagicMock()
        user_message.extend_info = None
        se.database_service.get_room_user_message_by_message_id.return_value = (
            user_message
        )
        se.database_service.update_room_user_message_by_message_id.return_value = True

        trajectory = SupervisorTrajectory()
        result = await se._checkpoint_trajectory("msg-1", trajectory)

        assert result is user_message
        assert isinstance(user_message.extend_info, dict)

    @pytest.mark.asyncio
    async def test_does_not_raise_on_db_error(self):
        """Checkpoint failures should be logged but not abort the loop."""
        se = _make_supervisor_executor()
        se.database_service.get_room_user_message_by_message_id.side_effect = (
            RuntimeError("DB connection lost")
        )

        trajectory = SupervisorTrajectory()
        result = await se._checkpoint_trajectory("msg-1", trajectory)
        assert result is None


# =============================================================================
# CLARIFY cleanup compensation Tests
# =============================================================================


class TestClarifyCleanupCompensation:
    """Tests that the CLARIFY handler cleans up HITL requests and messages
    when _save_interrupted_state fails or request_input returns None mid-group."""

    @pytest.fixture
    def se(self):
        return _make_supervisor_executor()

    def _make_room_config(self):
        cfg = MagicMock()
        cfg.is_debate_mode = False
        return cfg

    @pytest.mark.asyncio
    async def test_cancels_requests_when_save_interrupted_state_fails(self, se):
        """If all questions are created but continuation save fails,
        all HITL requests and messages must be cleaned up."""
        from models.supervisor_v2 import (
            SupervisorAction, ActionType, ClarifyQuestion,
        )

        req_a = MagicMock()
        req_a.request_id = "req-a"
        req_b = MagicMock()
        req_b.request_id = "req-b"

        hitl_mock = AsyncMock()
        hitl_mock.request_input = AsyncMock(side_effect=[req_a, req_b])
        hitl_mock.cancel_request = AsyncMock()

        agent_msg = MagicMock(message_id="msg-agent-1")
        se.room_services.create_agent_message.return_value = agent_msg
        se.database_service.add_room_agent_message = AsyncMock()
        se.database_service.delete_room_agent_message_by_message_id = AsyncMock()

        action = SupervisorAction(
            action=ActionType.CLARIFY,
            reasoning="need info",
            questions=[
                ClarifyQuestion(prompt="Q1?"),
                ClarifyQuestion(prompt="Q2?"),
            ],
        )

        se.supervisor_service.decide_next = AsyncMock(return_value=action)
        se._save_interrupted_state = AsyncMock(return_value=False)
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        se.hitl_coordinator = hitl_mock
        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Hello",
            agent_registry=[],
            room_config=self._make_room_config(),
            request_user_id="user-1",
        )

        assert result.status == "failed"
        assert hitl_mock.cancel_request.await_count == 2
        hitl_mock.cancel_request.assert_any_await("req-a", "room-1")
        hitl_mock.cancel_request.assert_any_await("req-b", "room-1")

    @pytest.mark.asyncio
    async def test_cancels_prior_requests_when_request_input_returns_none(self, se):
        """If request_input returns None mid-group (e.g. max rounds),
        previously created requests must be canceled."""
        from models.supervisor_v2 import (
            SupervisorAction, ActionType, ClarifyQuestion,
        )

        req_a = MagicMock()
        req_a.request_id = "req-a"

        hitl_mock = AsyncMock()
        hitl_mock.request_input = AsyncMock(side_effect=[req_a, None])
        hitl_mock.cancel_request = AsyncMock()

        agent_msg = MagicMock(message_id="msg-agent-1")
        se.room_services.create_agent_message.return_value = agent_msg
        se.database_service.add_room_agent_message = AsyncMock()
        se.database_service.delete_room_agent_message_by_message_id = AsyncMock()

        action = SupervisorAction(
            action=ActionType.CLARIFY,
            reasoning="need info",
            questions=[
                ClarifyQuestion(prompt="Q1?"),
                ClarifyQuestion(prompt="Q2?"),
            ],
        )

        se.supervisor_service.decide_next = AsyncMock(return_value=action)
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        se.hitl_coordinator = hitl_mock
        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Hello",
            agent_registry=[],
            room_config=self._make_room_config(),
            request_user_id="user-1",
        )

        assert result.status == "failed"
        assert hitl_mock.cancel_request.await_count == 1
        hitl_mock.cancel_request.assert_awaited_once_with("req-a", "room-1")
        assert se.database_service.delete_room_agent_message_by_message_id.await_count == 2


class TestProcessingStatusLifecycleOrder:
    @pytest.mark.asyncio
    async def test_stage_notification_records_before_send(self):
        se = _make_supervisor_executor()
        order: list[str] = []
        emit = AsyncMock(side_effect=lambda *a, **k: order.append("emit"))
        se.bind_execution_event_deps(emit)
        se.supervisor_service.decide_next = AsyncMock(
            return_value=SupervisorAction(
                action=ActionType.DONE,
                reasoning="nothing else to do",
            )
        )
        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[],
            room_config=RoomConfig(),
        )

        assert result.status == RunStatus.COMPLETED
        emit.assert_awaited_once()
        se.sse_manager.send_processing_status.assert_not_called()
        assert order == ["emit"]

    @pytest.mark.asyncio
    async def test_stage_notification_helper_failure_is_swallowed(self):
        se = _make_supervisor_executor()
        se.sse_manager.send_processing_status = AsyncMock()
        emit = AsyncMock(side_effect=RuntimeError("lifecycle unavailable"))
        se.bind_execution_event_deps(emit)
        se.supervisor_service.decide_next = AsyncMock(
            return_value=SupervisorAction(
                action=ActionType.DONE,
                reasoning="nothing else to do",
            )
        )
        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[],
            room_config=RoomConfig(),
        )

        assert result.status == RunStatus.COMPLETED
        emit.assert_awaited_once()
        se.sse_manager.send_processing_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_awaiting_input_records_before_send(self):
        se = _make_supervisor_executor()
        order: list[str] = []
        emit = AsyncMock(
            side_effect=lambda *a, **k: order.append("emit")
            if k.get("status") == "awaiting_input"
            else None
        )
        se.bind_execution_event_deps(emit)
        se.supervisor_service.decide_next = AsyncMock(
            return_value=SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="ask agent",
                targets=[
                    DelegateTarget(
                        agent_id="agent-1",
                        agent_name="Agent",
                        task="ask",
                    )
                ],
            )
        )
        se._dispatch_targets = AsyncMock(
            return_value=[
                V2StepResult(
                    step_number=1,
                    agent_id="agent-1",
                    agent_name="Agent",
                    task="ask",
                    response_text="",
                    success=False,
                    status=StepStatus.AWAITING_INPUT,
                    paused_message_id="agent-msg-1",
                    agent_message_id="agent-msg-1",
                    status_message="Need input",
                )
            ]
        )
        se._checkpoint_trajectory = AsyncMock(return_value=None)
        se._save_interrupted_state = AsyncMock(return_value=True)
        hitl_mock = AsyncMock()
        hitl_mock.request_input = AsyncMock(
            return_value=SimpleNamespace(request_id="hitl-1")
        )

        se.hitl_coordinator = hitl_mock
        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[
                AgentProfile(agent_id="agent-1", agent_name="Agent")
            ],
            room_config=RoomConfig(),
        )

        assert result.status == RunStatus.AWAITING_INPUT
        assert order == ["emit"]
        se.sse_manager.send_processing_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_supervisor_hitl_records_before_awaiting_input_send(self):
        se = _make_supervisor_executor()
        order: list[str] = []
        emit = AsyncMock(
            side_effect=lambda *a, **k: order.append("emit")
            if k.get("status") == "awaiting_input"
            else None
        )
        se.bind_execution_event_deps(emit)
        se.supervisor_service.decide_next = AsyncMock(
            return_value=SupervisorAction(
                action=ActionType.CLARIFY,
                reasoning="need details",
                questions=[ClarifyQuestion(prompt="Which account?")],
            )
        )
        se.room_services.create_agent_message.return_value = SimpleNamespace(
            message_id="hitl-agent-msg"
        )
        se.database_service.resolve_client_request_id_for_message_id = AsyncMock(
            return_value="cr-1"
        )
        se.database_service.add_room_agent_message = AsyncMock()
        se._save_interrupted_state = AsyncMock(return_value=True)
        hitl_mock = AsyncMock()
        hitl_mock.request_input = AsyncMock(
            return_value=SimpleNamespace(request_id="hitl-1")
        )

        se.hitl_coordinator = hitl_mock
        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[],
            room_config=RoomConfig(),
        )

        assert result.status == RunStatus.AWAITING_INPUT
        assert order == ["emit"]
        se.sse_manager.send_processing_status.assert_not_called()
