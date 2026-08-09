"""
Unit tests for RoomMessageCenter module.

Tests cover:
- _validate_room_message_request: input validation
- _find_paused_agent: trajectory search
- _extract_clarify_question: clarification extraction
- _append_paused_result_to_trajectory: in-place mutation
"""

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.types import TaskState

from common.a2a_constants import SSEProcessingStatus
from common.utils.cancellation import CancellationToken
from common.utils.time import utcnow
from execution.cancellation import CancellationConfig, CancellationRuntime
from execution.orchestration.queue_executor import (
    QueueExecutor,
    QueueResult,
    ResumeResult,
)
from execution.orchestration.room_message_center import RoomMessageCenter
from execution.orchestration.run_store import InMemoryOrchestrationRunStore
from execution.shutdown import GRACEFUL_SHUTDOWN_CANCEL_REASON
from models.agent import AgentStatus
from models.orchestration import (
    AgentOutputRecord,
    DispatchIntent,
    OrchestrationRunState,
    OrchestrationStatus,
)
from models.response import OrchestrationResponse
from models.room import MessageContent, Room, RoomAgentMessage, RoomUserMessage
from models.supervisor import (
    ActionType,
    RunStatus,
    StepResult,
    StepStatus,
    SupervisorAction,
    SupervisorRunResult,
    SupervisorTrajectory,
    TrajectoryEntry,
)


@pytest.mark.asyncio
async def test_processing_claim_heartbeat_refreshes_until_cancelled(monkeypatch):
    from execution.orchestration import room_message_center as module

    rmc = object.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    rmc.orphan_threshold_minutes = 2
    rmc.message_writer = SimpleNamespace(
        refresh_processing_claim=AsyncMock(return_value=True)
    )
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError])
    monkeypatch.setattr(module.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await rmc._heartbeat_processing_claim("message-1")

    rmc.message_writer.refresh_processing_claim.assert_awaited_once_with("message-1")


@pytest.mark.asyncio
async def test_duplicate_claim_loser_does_not_release_winner_token():
    rmc = object.__new__(RoomMessageCenter)
    winner = CancellationToken(message_id="message-1")
    rmc.cancellation_control = make_cancellation_control(winner)
    rmc._validate_room_message_request = MagicMock(return_value=None)
    rmc._claim_user_message = AsyncMock(return_value=False)
    request = SimpleNamespace(
        room_id="room-1",
        room_user_message_id="message-1",
        is_recovery=False,
    )

    response = await rmc.process_room_user_message(request)

    assert response.status_code == 409
    rmc.cancellation_control.create_token.assert_not_called()
    rmc.cancellation_control.release_token.assert_not_called()


@pytest.mark.asyncio
async def test_execution_entry_hydrates_redis_tombstone_before_token_checkpoint():
    rmc = object.__new__(RoomMessageCenter)
    token = CancellationToken(message_id="message-1")
    control = make_cancellation_control(token)

    async def hydrate(_message_id):
        token.cancel()
        return True

    control.check_cancelled = AsyncMock(side_effect=hydrate)
    rmc.cancellation_control = control
    rmc._validate_room_message_request = MagicMock(return_value=None)
    rmc._claim_user_message = AsyncMock(return_value=True)
    rmc._acquire_room_lock = AsyncMock(return_value=None)
    rmc.message_writer = SimpleNamespace(
        unclaim_user_message=AsyncMock(),
    )
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc._emit_processing_status = AsyncMock()
    request = SimpleNamespace(
        room_id="room-1",
        room_user_message_id="message-1",
        is_recovery=True,
    )

    response = await rmc.process_room_user_message(request)

    assert response.status_code == 429
    assert token.is_cancelled is True
    control.check_cancelled.assert_awaited_once_with("message-1")
    control.release_token.assert_called_once_with("message-1", token)


# =============================================================================
# _validate_room_message_request Tests
# =============================================================================


def make_cancellation_control(token=None):
    resolved = token or CancellationToken(message_id="user-msg-1")
    control = MagicMock()
    control.create_token.return_value = resolved
    control.get_token.return_value = resolved
    control.check_cancelled = AsyncMock(return_value=False)
    control.release_token.return_value = True
    return control


class RecordingEventPublisher:
    def __init__(self):
        self.internal_events = []

    async def publish(
        self,
        event,
        *,
        wait_for_handlers: bool = False,
        fanout: bool = True,
    ):
        self.internal_events.append(event)


class TestValidateRoomMessageRequest:
    """Tests for orchestration request validation."""

    @pytest.fixture
    def rmc(self):
        return RoomMessageCenter.__new__(RoomMessageCenter)

    def test_returns_none_for_valid_request(self, rmc):
        req = MagicMock()
        req.room_id = "room-001"
        req.room_user_message_id = "msg-001"
        assert rmc._validate_room_message_request(req) is None

    def test_returns_error_when_room_id_missing(self, rmc):
        req = MagicMock()
        req.room_id = None
        req.room_user_message_id = "msg-001"
        result = rmc._validate_room_message_request(req)
        assert result is not None
        assert result.success is False
        assert result.status_code == 400
        assert "room" in result.error.lower()

    def test_returns_error_when_message_id_missing(self, rmc):
        req = MagicMock()
        req.room_id = "room-001"
        req.room_user_message_id = None
        result = rmc._validate_room_message_request(req)
        assert result is not None
        assert result.success is False
        assert result.status_code == 400
        assert "message" in result.error.lower()

    def test_returns_error_when_both_missing(self, rmc):
        req = MagicMock()
        req.room_id = None
        req.room_user_message_id = None
        result = rmc._validate_room_message_request(req)
        assert result is not None
        assert result.success is False


class TestRoomFacadeBinding:
    def test_unbound_room_facade_fails_fast(self):
        rmc = RoomMessageCenter.__new__(RoomMessageCenter)
        rmc.cancellation_control = make_cancellation_control()
        rmc._room_facade = None
        rmc._room_bound = False

        with pytest.raises(
            RuntimeError,
            match=r"RoomMessageCenter\.bind_facade\(\) not called - startup incomplete",
        ):
            rmc._require_room_facade()

    def test_bind_facade_makes_room_persistence_available(self):
        rmc = RoomMessageCenter.__new__(RoomMessageCenter)
        rmc.cancellation_control = make_cancellation_control()
        facade = MagicMock()

        rmc.bind_facade(facade)

        assert rmc._require_room_facade() is facade


@pytest.mark.asyncio
async def test_async_agent_callback_reenters_durable_orchestration_without_continuation():
    store = InMemoryOrchestrationRunStore()
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="Coordinate this",
        candidate_agent_ids=["agent-1"],
        status=OrchestrationStatus.WAITING_AGENT,
        dispatch_intents=[
            DispatchIntent(
                step_id="run-1:step-1",
                step_target_id="run-1:step-1:target-1",
                dispatch_intent_id="run-1:step-1:target-1:intent",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Handle this",
                task_hash="hash",
            )
        ],
    )
    await store.create_run(state)
    agent_message = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-msg-1",
        agent_id="agent-1",
        user_id="user-1",
        related_message_id="user-msg-1",
        message_content=MessageContent(message_text="Webhook result"),
    )
    rmc = object.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    rmc.continuation_store = SimpleNamespace(
        get_pending_continuation_on_message=AsyncMock(return_value=None)
    )
    rmc.message_reader = SimpleNamespace(
        get_room_agent_message_by_message_id=AsyncMock(return_value=agent_message)
    )
    rmc.orchestration_run_store = store
    rmc.process_room_user_message = AsyncMock(
        return_value=OrchestrationResponse(room_id="room-1", success=True)
    )

    resumed = await rmc.resume_queue_from_continuation("agent-msg-1")

    assert resumed is True
    request = rmc.process_room_user_message.await_args.args[0]
    assert request.room_id == "room-1"
    assert request.room_user_message_id == "user-msg-1"
    assert request.is_recovery is True
    assert request.reuse_processing_claim is True


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [["bad"], [], "bad", ""])
async def test_resume_top_level_malformed_continuation_is_restored(malformed):
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.continuation_store = SimpleNamespace(
        get_pending_continuation_on_message=AsyncMock(return_value=malformed),
        get_and_clear_continuation_on_message=AsyncMock(return_value=malformed),
    )
    restore = AsyncMock()
    rmc.queue_executor = SimpleNamespace(_restore_invalid_continuation=restore)

    assert not await rmc.resume_queue_from_continuation("agent-msg-1")
    restore.assert_awaited_once_with(
        "agent-msg-1",
        malformed,
        reason="continuation payload must be an object",
    )


@pytest.mark.asyncio
async def test_failed_supervisor_result_projects_terminal_summary_to_client_boundaries():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    summary = {
        "code": "orchestration_failed",
        "reason": "delegate_no_progress_repeat",
        "recommended_next_action": "ask_user",
    }
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="user-msg-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Produce a quote."),
        extend_info={
            "orchestration": True,
            "orchestration_run_id": "run-1",
        },
        processing_claimed_at=utcnow(),
    )
    run_state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="Produce a quote.",
        candidate_agent_ids=["agent-1"],
        status=OrchestrationStatus.FAILED,
        terminal_reason="delegate_no_progress_repeat",
        terminal_summary=summary,
    )
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message),
    )
    rmc.message_writer = SimpleNamespace(
        update_room_user_message_by_message_id=AsyncMock(),
        cancel_agent_messages_by_ids=AsyncMock(),
        cancel_descendants=AsyncMock(),
    )
    rmc._run_supervisor_terminal_post_loop_integration = AsyncMock()
    rmc._turn_event_appender = SimpleNamespace(append=AsyncMock())
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc._emit_processing_status = AsyncMock()
    rmc.delivery = SimpleNamespace(remove_token=MagicMock())

    await rmc._handle_supervisor_run_result(
        SupervisorRunResult(
            status=RunStatus.FAILED,
            run_state=run_state,
            terminal_reason=run_state.terminal_reason,
            terminal_summary=summary,
        ),
        "room-1",
        "user-msg-1",
        user_message=user_message,
    )

    assert user_message.extend_info["terminal_summary"] == summary
    assert user_message.processing_claimed_at is None
    rmc._turn_event_appender.append.assert_not_awaited()
    rmc._emit_processing_status.assert_awaited_once_with(
        room_id="room-1",
        status=SSEProcessingStatus.FAILED,
        message_id="user-msg-1",
        lifecycle_message_id="user-msg-1",
        details={
            "message": "delegate_no_progress_repeat",
            "code": "orchestration_failed",
            "terminal_summary": summary,
        },
        system_message_id=None,
        turn_event_enabled=True,
    )


@pytest.mark.asyncio
async def test_graceful_shutdown_while_waiting_for_room_lock_leaves_claim_for_recovery():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    request = SimpleNamespace(
        room_id="room-1",
        room_user_message_id="user-msg-1",
        is_recovery=False,
    )
    rmc.message_writer = SimpleNamespace(
        claim_user_message_for_processing=AsyncMock(return_value=True),
        refresh_processing_claim=AsyncMock(),
    )
    rmc._acquire_room_lock = AsyncMock(
        side_effect=asyncio.CancelledError(GRACEFUL_SHUTDOWN_CANCEL_REASON)
    )
    rmc._emit_processing_status = AsyncMock()
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await rmc.process_room_user_message(request)

    assert exc_info.value.args == (GRACEFUL_SHUTDOWN_CANCEL_REASON,)
    rmc.message_writer.claim_user_message_for_processing.assert_awaited_once_with(
        "user-msg-1"
    )
    rmc.message_writer.refresh_processing_claim.assert_not_awaited()
    rmc._emit_processing_status.assert_not_awaited()
    rmc._notify_all_non_terminal_tasks_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_room_user_message_cancelled_error_emits_canceled_and_reraises():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    request = SimpleNamespace(
        room_id="room-1",
        room_user_message_id="user-msg-1",
        is_recovery=False,
    )
    rmc.message_writer = SimpleNamespace(
        claim_user_message_for_processing=AsyncMock(return_value=True),
        refresh_processing_claim=AsyncMock(),
    )
    rmc._acquire_room_lock = AsyncMock(return_value="owner-1")
    rmc._release_room_lock = AsyncMock()
    rmc._process_room_user_message_locked = AsyncMock(
        side_effect=asyncio.CancelledError()
    )
    order: list[str] = []
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock(
        side_effect=lambda *_args: order.append("cleanup")
    )
    rmc._emit_processing_status = AsyncMock(
        side_effect=lambda **_kwargs: order.append("root")
    )
    rmc.delivery = SimpleNamespace(clear_cancellation=MagicMock())
    rmc._turn_event_appender = SimpleNamespace(append=AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        await rmc.process_room_user_message(request)

    rmc._turn_event_appender.append.assert_not_awaited()
    rmc._notify_all_non_terminal_tasks_failed.assert_not_awaited()
    rmc._emit_processing_status.assert_awaited_once_with(
        room_id="room-1",
        status=SSEProcessingStatus.CANCELED,
        message_id="user-msg-1",
        lifecycle_message_id="user-msg-1",
        system_message_id="sys-user-msg-1",
    )
    rmc.cancellation_control.clear_cancellation.assert_not_called()
    assert order == ["root"]
    rmc._release_room_lock.assert_awaited_once_with(
        "room-1",
        "owner-1",
        acquired_at=pytest.approx(
            rmc._release_room_lock.call_args.kwargs["acquired_at"]
        ),
    )


@pytest.mark.asyncio
async def test_lock_timeout_uses_only_durable_winner_cleanup():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    request = SimpleNamespace(
        room_id="room-1",
        room_user_message_id="user-msg-1",
        is_recovery=False,
    )
    rmc._claim_user_message = AsyncMock(return_value=True)
    rmc._acquire_room_lock = AsyncMock(return_value=None)
    rmc.message_writer = SimpleNamespace(
        unclaim_user_message=AsyncMock(return_value=True)
    )
    rmc._emit_processing_status = AsyncMock(return_value={"event_id": "terminal"})
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock(
        side_effect=RuntimeError("child cleanup failed")
    )

    response = await rmc.process_room_user_message(request)

    assert response.success is False
    rmc._emit_processing_status.assert_awaited_once()
    rmc._notify_all_non_terminal_tasks_failed.assert_not_awaited()
    assert rmc._emit_processing_status.await_args.kwargs["system_message_id"] == (
        "sys-user-msg-1"
    )
    rmc.message_writer.unclaim_user_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_supervisor_prep_uses_only_durable_winner_cleanup():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    token = CancellationToken(message_id="user-msg-1")
    rmc.cancellation_control = make_cancellation_control(token)
    rmc._turn_event_appender = None
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="user-msg-1",
        user_id="user-1",
        message_content=MessageContent(message_text="help"),
        extend_info={},
    )
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message)
    )
    rmc.room_runtime = SimpleNamespace(
        inquiry_agent_messages_by_related_message_id=AsyncMock(
            return_value=SimpleNamespace(success=True, message_list=[])
        )
    )
    rmc.room_reader = SimpleNamespace(
        get_room_by_room_id=AsyncMock(
            return_value=SimpleNamespace(extend_info={"use_supervisor": True})
        )
    )
    rmc._emit_processing_status = AsyncMock(return_value={"event_id": "terminal"})
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock(
        side_effect=RuntimeError("child cleanup failed")
    )

    response = await rmc._process_room_user_message_locked(
        SimpleNamespace(user_id="user-1", client_request_id="request-1"),
        "room-1",
        "user-msg-1",
        token=token,
    )

    assert response.success is False
    rmc._emit_processing_status.assert_awaited_once()
    rmc._notify_all_non_terminal_tasks_failed.assert_not_awaited()
    assert rmc._emit_processing_status.await_args.kwargs["system_message_id"] == (
        "sys-user-msg-1"
    )


@pytest.mark.asyncio
async def test_early_cancellation_uses_only_durable_winner_cleanup():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    token = CancellationToken(message_id="user-msg-1")
    token.cancel()
    rmc.cancellation_control = make_cancellation_control(token)
    rmc._turn_event_appender = None
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="user-msg-1",
        user_id="user-1",
        message_content=MessageContent(message_text="help"),
        extend_info={},
    )
    agent_message = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-msg-1",
        message_content=MessageContent(message_text="work"),
    )
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message)
    )
    rmc.room_runtime = SimpleNamespace(
        inquiry_agent_messages_by_related_message_id=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                message_list=[agent_message],
            )
        )
    )
    rmc.tsm = SimpleNamespace(
        cancel_remaining_queue=AsyncMock(
            side_effect=RuntimeError("queue cleanup failed")
        )
    )
    rmc._emit_processing_status = AsyncMock(return_value={"event_id": "terminal"})

    response = await rmc._process_room_user_message_locked(
        SimpleNamespace(user_id="user-1", client_request_id="request-1"),
        "room-1",
        "user-msg-1",
        token=token,
    )

    assert response.success is True
    rmc._emit_processing_status.assert_awaited_once()
    rmc.tsm.cancel_remaining_queue.assert_not_awaited()
    assert rmc._emit_processing_status.await_args.kwargs["status"] == (
        SSEProcessingStatus.CANCELED
    )


@pytest.mark.asyncio
async def test_graceful_shutdown_interrupt_leaves_turn_recoverable():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    request = SimpleNamespace(
        room_id="room-1",
        room_user_message_id="user-msg-1",
        is_recovery=False,
    )
    rmc.message_writer = SimpleNamespace(
        claim_user_message_for_processing=AsyncMock(return_value=True),
        refresh_processing_claim=AsyncMock(),
    )
    rmc._acquire_room_lock = AsyncMock(return_value="owner-1")
    rmc._release_room_lock = AsyncMock()
    rmc._process_room_user_message_locked = AsyncMock(
        side_effect=asyncio.CancelledError(GRACEFUL_SHUTDOWN_CANCEL_REASON)
    )
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc._emit_processing_status = AsyncMock()
    rmc.delivery = SimpleNamespace(clear_cancellation=MagicMock())
    rmc._turn_event_appender = SimpleNamespace(append=AsyncMock())

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await rmc.process_room_user_message(request)

    assert exc_info.value.args == (GRACEFUL_SHUTDOWN_CANCEL_REASON,)
    rmc._turn_event_appender.append.assert_not_awaited()
    rmc._notify_all_non_terminal_tasks_failed.assert_not_awaited()
    rmc._emit_processing_status.assert_not_awaited()
    rmc.cancellation_control.clear_cancellation.assert_not_called()
    rmc._release_room_lock.assert_awaited_once()


# =============================================================================
# _find_paused_agent Tests
# =============================================================================


def _make_trajectory_with_paused(agent_id="a1", agent_name="Agent1", msg_id="msg-p"):
    """Helper to build a trajectory with a PAUSED result."""
    from datetime import datetime

    entry = TrajectoryEntry(
        step_number=1,
        action=SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="test",
            targets=[
                {"agent_id": agent_id, "agent_name": agent_name, "task": "do stuff"}
            ],
        ),
        results=[
            StepResult(
                step_number=1,
                agent_id=agent_id,
                agent_name=agent_name,
                task="do stuff",
                response_text="",
                success=False,
                status=StepStatus.PAUSED,
                agent_message_id=msg_id,
            )
        ],
        started_at=datetime(2026, 1, 1),
    )
    t = SupervisorTrajectory()
    t.entries = [entry]
    return t


# =============================================================================
# Phase 7a handoff ordering proofs
# =============================================================================


_ROOT = Path(__file__).resolve().parents[1]
_RMC_PATH = _ROOT / "execution" / "orchestration" / "room_message_center.py"


def _source_tree() -> ast.AST:
    return ast.parse(_RMC_PATH.read_text(), filename=str(_RMC_PATH))


def _function_node(function_name: str) -> ast.AsyncFunctionDef:
    matches = [
        node
        for node in ast.walk(_source_tree())
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name
    ]
    assert matches, f"{function_name} not found"
    return matches[0]


def _call_line(
    function_name: str,
    call_name: str,
    *snippets: str,
    occurrence: int = 1,
) -> int:
    matches: list[tuple[int, str]] = []
    for node in ast.walk(_function_node(function_name)):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        if name != call_name:
            continue
        expression = ast.unparse(node)
        if all(snippet in expression for snippet in snippets):
            matches.append((node.lineno, expression))
    matches.sort()
    assert len(matches) >= occurrence, (
        f"{function_name}.{call_name} with {snippets!r} occurrence "
        f"{occurrence} not found; matches={matches}"
    )
    return matches[occurrence - 1][0]


def _matching_call(
    function_name: str,
    call_name: str,
    *snippets: str,
    occurrence: int = 1,
) -> ast.Call:
    matches: list[ast.Call] = []
    for node in ast.walk(_function_node(function_name)):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        if name != call_name:
            continue
        expression = ast.unparse(node)
        if all(snippet in expression for snippet in snippets):
            matches.append(node)
    matches.sort(key=lambda node: node.lineno)
    assert len(matches) >= occurrence, (
        f"{function_name}.{call_name} with {snippets!r} occurrence "
        f"{occurrence} not found; matches="
        f"{[(node.lineno, ast.unparse(node)) for node in matches]}"
    )
    return matches[occurrence - 1]


def _body_containing_statement(
    function: ast.AsyncFunctionDef,
    statement: ast.stmt,
) -> list[ast.stmt]:
    for node in ast.walk(function):
        bodies: list[list[ast.stmt]] = []
        for attr in ("body", "orelse", "finalbody"):
            body = getattr(node, attr, None)
            if isinstance(body, list) and all(
                isinstance(item, ast.stmt) for item in body
            ):
                bodies.append(body)
        if isinstance(node, ast.Try):
            bodies.extend(handler.body for handler in node.handlers)
        if isinstance(node, ast.Match):
            bodies.extend(case.body for case in node.cases)
        for body in bodies:
            if any(item is statement for item in body):
                return body
    raise AssertionError(
        f"body containing statement at line {statement.lineno} not found"
    )


def _statement_containing_call(
    function: ast.AsyncFunctionDef,
    call: ast.Call,
) -> ast.stmt:
    statements = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.stmt)
        and node.lineno <= call.lineno <= getattr(node, "end_lineno", node.lineno)
    ]
    assert statements, f"statement containing call at line {call.lineno} not found"
    statements.sort(
        key=lambda node: (
            getattr(node, "end_lineno", node.lineno) - node.lineno,
            -node.lineno,
        )
    )
    return statements[0]


def _statement_owning_body(
    function: ast.AsyncFunctionDef,
    body: list[ast.stmt],
) -> ast.stmt | None:
    if body is function.body:
        return None
    for node in ast.walk(function):
        if not isinstance(node, ast.stmt):
            continue
        bodies: list[list[ast.stmt]] = []
        for attr in ("body", "orelse", "finalbody"):
            candidate = getattr(node, attr, None)
            if isinstance(candidate, list) and all(
                isinstance(item, ast.stmt) for item in candidate
            ):
                bodies.append(candidate)
        if isinstance(node, ast.Try):
            bodies.extend(handler.body for handler in node.handlers)
        if isinstance(node, ast.Match):
            bodies.extend(case.body for case in node.cases)
        if any(candidate is body for candidate in bodies):
            return node
    raise AssertionError("owner for branch body not found")


def _preceding_path_statements(
    function: ast.AsyncFunctionDef,
    emit_statement: ast.stmt,
) -> list[ast.stmt]:
    path_statements: list[ast.stmt] = []
    current_statement: ast.stmt | None = emit_statement
    while current_statement is not None:
        body = _body_containing_statement(function, current_statement)
        emit_index = next(
            index
            for index, statement in enumerate(body)
            if statement is current_statement
        )
        path_statements.extend(body[:emit_index])
        current_statement = _statement_owning_body(function, body)
    return path_statements


def _path_calls(statement: ast.stmt) -> list[ast.Call]:
    if isinstance(statement, ast.If) and any(
        isinstance(node, ast.Return) for node in ast.walk(statement)
    ):
        return [node for node in ast.walk(statement.test) if isinstance(node, ast.Call)]
    return [node for node in ast.walk(statement) if isinstance(node, ast.Call)]


def _assert_before(
    function_name: str,
    before_call: str,
    before_snippets: tuple[str, ...],
    emit_snippets: tuple[str, ...],
    *,
    before_occurrence: int = 1,
    emit_occurrence: int = 1,
) -> None:
    function = _function_node(function_name)
    emit_call = _matching_call(
        function_name,
        "_emit_processing_status",
        *emit_snippets,
        occurrence=emit_occurrence,
    )
    emit_statement = _statement_containing_call(function, emit_call)
    path_statements = _preceding_path_statements(function, emit_statement)
    candidates: list[tuple[int, str]] = []
    for statement in path_statements:
        for node in _path_calls(statement):
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            else:
                name = None
            expression = ast.unparse(node)
            if name == before_call and all(
                snippet in expression for snippet in before_snippets
            ):
                candidates.append((node.lineno, expression))
    candidates.sort()
    assert len(candidates) >= before_occurrence, (
        f"{function_name}.{before_call} with {before_snippets!r} occurrence "
        f"{before_occurrence} not found in same path before emit line "
        f"{emit_call.lineno}; candidates={candidates}"
    )
    assert candidates[before_occurrence - 1][0] < emit_call.lineno


@pytest.mark.asyncio
async def test_failed_room_lock_defers_child_transition_to_durable_projection():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    request = SimpleNamespace(
        room_id="room-1",
        room_user_message_id="umsg-1",
        is_recovery=False,
    )
    task = SimpleNamespace(status=SimpleNamespace(state=TaskState.working))
    agent_message = SimpleNamespace(
        message_id="agent-1",
        user_id="user-1",
        has_task_tracking=True,
        message_content=SimpleNamespace(message_task=task),
    )
    rmc.message_writer = SimpleNamespace(
        claim_user_message_for_processing=AsyncMock(return_value=True),
        unclaim_user_message=AsyncMock(),
    )
    rmc.message_reader = SimpleNamespace(
        get_room_agent_messages_by_related_message_id=AsyncMock(
            return_value=[agent_message]
        ),
    )
    rmc._acquire_room_lock = AsyncMock(return_value=None)
    rmc.tsm = SimpleNamespace(
        transition_task=AsyncMock(side_effect=RuntimeError("task db unavailable"))
    )
    rmc.task_notifications = SimpleNamespace(notify_task_update=AsyncMock())
    rmc.delivery = SimpleNamespace(send_processing_status=AsyncMock())
    emit = AsyncMock()
    rmc._processing_status_emitter = emit

    result = await rmc.process_room_user_message(request)

    assert result.status_code == 429
    rmc.tsm.transition_task.assert_not_awaited()
    rmc.task_notifications.notify_task_update.assert_not_awaited()
    emit.assert_awaited_once()
    rmc.delivery.send_processing_status.assert_not_awaited()


def _supervisor_agent(agent_id: str, name: str):
    return SimpleNamespace(
        agent_id=agent_id,
        agent_card=SimpleNamespace(name=name, description="", skills=[]),
        call_count=0,
        call_success_count=0,
        agent_status=AgentStatus.active,
    )


def _completed_state_with_agent_outputs() -> OrchestrationRunState:
    return OrchestrationRunState(
        run_id="msg-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Need quote",
        candidate_agent_ids=["agent-1", "agent-2"],
        status=OrchestrationStatus.COMPLETED,
        dispatch_intents=[
            DispatchIntent(
                step_id="msg-1:step-1",
                step_target_id="msg-1:step-1:target-1",
                dispatch_intent_id="msg-1:step-1:target-1:intent",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Find pricing",
                task_hash="hash-1",
            ),
            DispatchIntent(
                step_id="msg-1:step-1",
                step_target_id="msg-1:step-1:target-2",
                dispatch_intent_id="msg-1:step-1:target-2:intent",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-2",
                task="Find timing",
                task_hash="hash-2",
            ),
        ],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status=StepStatus.SUCCESS.value,
                text="Pricing is $42.",
            ),
            AgentOutputRecord(
                agent_message_id="agent-msg-2",
                agent_id="agent-2",
                status=StepStatus.SUCCESS.value,
                text="Delivery is Friday.",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_supervisor_uses_single_run_entrypoint_for_orchestration_envelope(
    monkeypatch,
):
    calls = {"run": 0}

    class Executor:
        async def run(self, **kwargs):
            calls["run"] += 1
            from models.supervisor import RunStatus, SupervisorRunResult

            return SupervisorRunResult(
                status=RunStatus.COMPLETED,
                trajectory=None,
                run_id="msg-1",
            )

    center = RoomMessageCenter.__new__(RoomMessageCenter)
    center.supervisor_executor = Executor()
    center.supervisor_planning_error_cls = Exception
    center.build_turn_content = None
    center._build_supervisor_inputs = AsyncMock(
        return_value=(
            [SimpleNamespace(agent_id="agent-1", agent_name="Agent One")],
            SimpleNamespace(room_agent_set={"agent-1": "Agent One"}),
            None,
        )
    )
    center._handle_supervisor_run_result = AsyncMock()
    center._log_room_memory_stats = AsyncMock()
    user_message = SimpleNamespace(
        message_id="msg-1",
        user_id="user-1",
        client_request_id="cr-1",
        message_content=SimpleNamespace(message_text="Need quote", attachments=[]),
        extend_info={
            "orchestration": True,
            "orchestration_run_id": "msg-1",
            "candidate_scope_mode": "explicit_selection",
            "candidate_agent_ids": ["agent-1"],
            "candidate_scope_snapshot_version": 1,
        },
    )

    await center._process_supervisor(
        room_id="room-1",
        room_user_message_id="msg-1",
        user_message=user_message,
        user_id="user-1",
        quoted_text=None,
        token=None,
    )

    assert calls == {"run": 1}


@pytest.mark.asyncio
async def test_completed_state_run_result_does_not_duplicate_execution_finalization():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="msg-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Need quote"),
        extend_info={
            "orchestration_run_id": "msg-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
        },
    )
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message)
    )
    rmc.message_writer = SimpleNamespace(
        update_room_user_message_by_message_id=AsyncMock()
    )
    rmc._run_supervisor_terminal_post_loop_integration = AsyncMock()
    rmc._emit_unified_summary = AsyncMock(return_value=("supervisor", None))
    rmc._persist_turn_completion_kind = AsyncMock()
    rmc._emit_processing_status = AsyncMock(return_value={"accepted": True})
    rmc._turn_event_appender = None
    rmc.delivery = SimpleNamespace(remove_token=MagicMock())

    await rmc._handle_supervisor_run_result(
        SupervisorRunResult(
            status=RunStatus.COMPLETED,
            trajectory=None,
            run_id="msg-1",
            run_state=_completed_state_with_agent_outputs(),
            synthesis_text="Final synthesis",
        ),
        room_id="room-1",
        user_message_id="msg-1",
        user_message=user_message,
    )

    rmc._emit_unified_summary.assert_not_awaited()
    rmc._run_supervisor_terminal_post_loop_integration.assert_awaited_once()


def _supervisor_completion_race_center(token, emit_summary):
    center = RoomMessageCenter.__new__(RoomMessageCenter)
    center.cancellation_control = make_cancellation_control(token)
    center.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=None)
    )
    center.message_writer = SimpleNamespace(
        update_room_user_message_by_message_id=AsyncMock(),
        cancel_descendants=AsyncMock(),
        cancel_agent_messages_by_ids=AsyncMock(),
    )
    center._run_supervisor_terminal_post_loop_integration = AsyncMock()
    center._emit_unified_summary = emit_summary
    center._persist_turn_completion_kind = AsyncMock()
    center._emit_processing_status = AsyncMock(return_value={"accepted": True})
    center._turn_event_appender = SimpleNamespace(append=AsyncMock())
    center._notify_all_non_terminal_tasks_failed = AsyncMock()
    return center


@pytest.mark.asyncio
async def test_supervisor_canceled_token_preserves_durable_completed_winner():
    token = CancellationToken(message_id="msg-1")
    token.cancel()
    summary = AsyncMock(return_value=("supervisor", None))
    center = _supervisor_completion_race_center(token, summary)

    await center._handle_supervisor_run_result(
        SupervisorRunResult(
            status=RunStatus.COMPLETED,
            run_id="msg-1",
            run_state=_completed_state_with_agent_outputs(),
            synthesis_text="Final synthesis",
        ),
        room_id="room-1",
        user_message_id="msg-1",
        token=token,
    )

    summary.assert_not_awaited()
    statuses = [
        call.kwargs["status"] for call in center._emit_processing_status.await_args_list
    ]
    assert statuses == [SSEProcessingStatus.COMPLETED]
    center._turn_event_appender.append.assert_not_awaited()
    terminal_call = center._emit_processing_status.await_args
    assert terminal_call.kwargs["turn_event_enabled"] is True


@pytest.mark.asyncio
async def test_completed_state_run_result_adds_synthesis_history_with_projection():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    rmc.room_memory = SimpleNamespace(
        add_synthesis_to_history=AsyncMock(return_value="turn-1"),
    )
    rmc._update_room_summary_safe = AsyncMock()
    rmc._trigger_compaction_safe = AsyncMock()

    await rmc._run_supervisor_terminal_post_loop_integration(
        SupervisorRunResult(
            status=RunStatus.COMPLETED,
            trajectory=None,
            run_id="msg-1",
            run_state=_completed_state_with_agent_outputs(),
            synthesis_text="Final synthesis",
        ),
        room_id="room-1",
    )

    rmc.room_memory.add_synthesis_to_history.assert_awaited_once()
    memory_kwargs = rmc.room_memory.add_synthesis_to_history.await_args.kwargs
    projected_texts = [
        step.response_text
        for entry in memory_kwargs["trajectory"].entries
        for step in entry.results
    ]
    assert "Pricing is $42." in projected_texts
    rmc._update_room_summary_safe.assert_awaited_once_with(
        "room-1",
        "Final synthesis",
        "turn-1",
    )


@pytest.mark.parametrize(
    ("run_status", "orchestration_status"),
    [
        (RunStatus.FAILED, OrchestrationStatus.FAILED),
        (RunStatus.CANCELED, OrchestrationStatus.CANCELED),
    ],
)
@pytest.mark.asyncio
async def test_terminal_state_run_result_defers_cleanup_to_durable_projection(
    run_status,
    orchestration_status,
):
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="msg-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Need quote"),
        extend_info={
            "orchestration_run_id": "msg-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
        },
    )
    state = _completed_state_with_agent_outputs()
    state.status = orchestration_status
    if run_status == RunStatus.FAILED:
        state.terminal_summary = {
            "code": "orchestration_failed",
            "recommended_next_action": "retry_or_fail",
        }
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message)
    )
    rmc.message_writer = SimpleNamespace(
        update_room_user_message_by_message_id=AsyncMock(),
        cancel_descendants=AsyncMock(),
        cancel_agent_messages_by_ids=AsyncMock(),
    )
    rmc._run_supervisor_terminal_post_loop_integration = AsyncMock()
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc._emit_processing_status = AsyncMock()
    rmc._turn_event_appender = None
    rmc.delivery = SimpleNamespace(
        clear_cancellation=MagicMock(),
        remove_token=MagicMock(),
    )

    await rmc._handle_supervisor_run_result(
        SupervisorRunResult(
            status=run_status,
            trajectory=None,
            run_id="msg-1",
            run_state=state,
        ),
        room_id="room-1",
        user_message_id="msg-1",
        user_message=user_message,
    )

    rmc.message_writer.cancel_descendants.assert_not_awaited()
    rmc.message_writer.cancel_agent_messages_by_ids.assert_not_awaited()
    rmc._notify_all_non_terminal_tasks_failed.assert_not_awaited()
    if run_status == RunStatus.FAILED:
        assert user_message.extend_info["terminal_summary"] == {
            "code": "orchestration_failed",
            "recommended_next_action": "retry_or_fail",
        }
    else:
        assert "terminal_summary" not in user_message.extend_info


@pytest.mark.asyncio
async def test_orchestration_envelope_routes_to_supervisor_executor():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.cancellation_control = make_cancellation_control()
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_run_id": "message-1",
            "candidate_scope_mode": "explicit_selection",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "candidate_scope_snapshot_version": 1,
            "mentioned_agent_ids": ["agent-2"],
            "client_request_id": "client-1",
        },
    )
    agents = {
        "agent-1": _supervisor_agent("agent-1", "Agent One"),
        "agent-2": _supervisor_agent("agent-2", "Agent Two"),
    }
    token = SimpleNamespace(is_cancelled=False)
    rmc.cancellation_control = make_cancellation_control(token)
    supervisor_result = SimpleNamespace(
        status=RunStatus.COMPLETED,
        trajectory=SimpleNamespace(clarify_original_message_id=None),
    )
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message),
        get_quoted_snippet_by_id=AsyncMock(return_value=None),
    )
    rmc.message_writer = SimpleNamespace()
    rmc._turn_event_appender = None
    rmc.delivery = SimpleNamespace(
        get_token=MagicMock(return_value=token),
        create_token=MagicMock(return_value=token),
        remove_token=MagicMock(),
    )
    rmc.room_runtime = SimpleNamespace(
        inquiry_agent_messages_by_related_message_id=AsyncMock(
            side_effect=AssertionError(
                "orchestration envelope should not use queue path"
            )
        )
    )
    rmc.agent_lookup = SimpleNamespace(
        get_agent_by_agent_id=AsyncMock(side_effect=lambda aid: agents.get(aid))
    )
    rmc.room_reader = SimpleNamespace(
        get_room_by_room_id=AsyncMock(
            return_value=Room(
                room_id="room-1",
                room_name="Room",
                room_owner_id="user-1",
                room_owner_name="User",
                room_agent_set={"agent-1": "Agent One", "agent-2": "Agent Two"},
                extend_info={"use_supervisor": True, "debateMode": False},
            )
        )
    )
    rmc.supervisor_executor = SimpleNamespace(
        run=AsyncMock(return_value=supervisor_result),
    )
    rmc.supervisor_planning_error_cls = RuntimeError
    rmc.build_turn_content = None
    rmc._handle_supervisor_run_result = AsyncMock()
    rmc._log_room_memory_stats = AsyncMock()
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc._emit_processing_status = AsyncMock()
    request = SimpleNamespace(
        room_id="room-1",
        room_user_message_id="message-1",
        user_id="user-1",
        client_request_id="client-1",
    )

    response = await rmc._process_room_user_message_locked(
        request,
        "room-1",
        "message-1",
        token=token,
    )

    assert response == OrchestrationResponse(
        room_id="room-1",
        success=True,
        error=None,
        status_code=200,
    )
    rmc.room_runtime.inquiry_agent_messages_by_related_message_id.assert_not_awaited()
    rmc.supervisor_executor.run.assert_awaited_once()
    run_kwargs = rmc.supervisor_executor.run.await_args.kwargs
    assert [agent.agent_id for agent in run_kwargs["agent_registry"]] == [
        "agent-1",
        "agent-2",
    ]
    assert run_kwargs["room_config"].room_agent_set == {
        "agent-1": "Agent One",
        "agent-2": "Agent Two",
    }
    assert run_kwargs["room_config"].explicit_mentions == [
        {
            "agent_id": "agent-2",
            "agent_name": "Agent Two",
            "mention_text": "<@agent-2|Agent Two>",
        }
    ]
    assert run_kwargs["conversation_context"] is None


@pytest.mark.parametrize(
    ("function_name", "details"),
    [
        ("process_room_user_message", "Room is busy processing another message"),
        (
            "_process_room_user_message_locked",
            "Supervisor-enabled room missing supervisor preparation data",
        ),
    ],
)
def test_early_failure_defers_child_cleanup_to_durable_projection(
    function_name, details
):
    function = _function_node(function_name)
    root_call = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_emit_processing_status"
        and details in ast.unparse(node)
    )
    assert root_call.lineno > 0
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_notify_all_non_terminal_tasks_failed"
        for node in ast.walk(function)
    )


def test_queue_canceled_records_durable_projection_in_terminal_emit():
    source = inspect.getsource(QueueExecutor.process_queue)
    assert "system_message_id=sys_message_id" in source
    assert "turn_event_enabled=bool(" in source
    assert "clear_cancellation(user_message_id)" not in source


def test_queue_failure_has_no_imperative_child_cleanup():
    function = _function_node("_process_room_user_message_locked")
    source = ast.unparse(function)

    assert "Failed to process agent messages" in source
    assert "_notify_all_non_terminal_tasks_failed" not in source
    assert "cancel_descendants" not in source


def _continuation_completion_race_center(token, emit_summary, appender):
    center = RoomMessageCenter.__new__(RoomMessageCenter)
    center.cancellation_control = make_cancellation_control(token)
    center.continuation_store = SimpleNamespace(
        save_continuation_on_message=AsyncMock(return_value=True)
    )
    center.queue_executor = SimpleNamespace(
        resume_from_continuation=AsyncMock(
            return_value=ResumeResult(
                success=True,
                needs_completion=True,
                room_id="room-1",
                user_message_id="user-msg-1",
                token=token,
            )
        )
    )
    center.room_reader = SimpleNamespace(
        get_room_by_room_id=AsyncMock(return_value=None)
    )
    center._emit_unified_summary = emit_summary
    center._turn_event_appender = appender
    center._persist_turn_completion_kind = AsyncMock()
    center._emit_processing_status = AsyncMock(return_value={"accepted": True})
    center._log_room_memory_stats = AsyncMock()
    center._notify_all_non_terminal_tasks_failed = AsyncMock()
    return center


@pytest.mark.asyncio
async def test_final_continuation_cancel_during_summary_suppresses_completion():
    token = CancellationToken(message_id="user-msg-1")
    entered = asyncio.Event()

    async def summary(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    appender = SimpleNamespace(append=AsyncMock())
    center = _continuation_completion_race_center(token, summary, appender)
    resuming = asyncio.create_task(
        center._resume_continuation_locked({}, "agent-msg-1", None)
    )
    await entered.wait()
    token.cancel()
    assert await resuming is True

    statuses = [
        call.kwargs["status"] for call in center._emit_processing_status.await_args_list
    ]
    assert statuses == [SSEProcessingStatus.CANCELED]
    appender.append.assert_not_awaited()
    assert (
        center._emit_processing_status.await_args.kwargs["turn_event_enabled"] is True
    )
    center._persist_turn_completion_kind.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_continuation_cancel_after_last_check_before_cas_obeys_winner():
    token = CancellationToken(message_id="user-msg-1")
    appender = SimpleNamespace(append=AsyncMock())
    center = _continuation_completion_race_center(
        token,
        AsyncMock(return_value=("deterministic", None)),
        appender,
    )
    child_terminal = AsyncMock()
    center.queue_executor._terminalize_system_task = child_terminal

    durable_cancellation_won = False

    async def emit_status(**kwargs):
        nonlocal durable_cancellation_won
        if kwargs["status"] == SSEProcessingStatus.COMPLETED:
            durable_cancellation_won = True
            return None
        return {"accepted": True}

    async def hydrate_cancel(_message_id):
        if durable_cancellation_won:
            token.cancel()
            return True
        return False

    center._emit_processing_status.side_effect = emit_status
    center.cancellation_control.check_cancelled.side_effect = hydrate_cancel

    assert await center._resume_continuation_locked({}, "agent-msg-1", None) is True

    statuses = [
        call.kwargs["status"] for call in center._emit_processing_status.await_args_list
    ]
    assert statuses == [
        SSEProcessingStatus.COMPLETED,
        SSEProcessingStatus.CANCELED,
    ]
    appender.append.assert_not_awaited()
    assert (
        center._emit_processing_status.await_args.kwargs["turn_event_enabled"] is True
    )
    center._persist_turn_completion_kind.assert_awaited_once_with(
        "user-msg-1",
        "deterministic",
    )
    child_terminal.assert_not_awaited()


def test_v1_resume_failure_has_no_imperative_child_cleanup_callback():
    source = inspect.getsource(RoomMessageCenter._resume_continuation_locked)

    assert "before_terminal_failure" not in source
    assert "_notify_all_non_terminal_tasks_failed" not in source


def _queue_completion_race_center(token, process_queue, emit_summary):
    center = RoomMessageCenter.__new__(RoomMessageCenter)
    center.cancellation_control = make_cancellation_control(token)
    center.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=None)
    )
    center.room_runtime = SimpleNamespace(
        inquiry_agent_messages_by_related_message_id=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                error=None,
                message_list=[SimpleNamespace(message_id="agent-msg-1")],
            )
        )
    )
    center.queue_executor = SimpleNamespace(process_queue=process_queue)
    center.room_reader = SimpleNamespace(
        get_room_by_room_id=AsyncMock(return_value=None)
    )
    center._emit_unified_summary = emit_summary
    center._turn_event_appender = SimpleNamespace(append=AsyncMock())
    center._emit_processing_status = AsyncMock(return_value={"accepted": True})
    center._persist_turn_completion_kind = AsyncMock()
    center._log_room_memory_stats = AsyncMock()
    return center


@pytest.mark.asyncio
async def test_many_paused_queues_do_not_retain_active_tokens():
    runtime = CancellationRuntime(
        collection=SimpleNamespace(),
        redis_kv=None,
        transport=None,
        config=CancellationConfig(),
        task_runner=lambda coro, *, name=None: asyncio.create_task(coro, name=name),
    )

    for index in range(100):
        message_id = f"message-{index}"
        token = runtime.create_token(message_id)
        center = _queue_completion_race_center(
            token,
            AsyncMock(return_value=SimpleNamespace(result=QueueResult.PAUSED)),
            AsyncMock(),
        )
        center.cancellation_control = runtime
        response = await center._process_room_user_message_locked(
            SimpleNamespace(user_id="user-1", client_request_id=f"cr-{index}"),
            "room-1",
            message_id,
            token=token,
        )
        assert response.success is True

    assert runtime.active_token_count == 0


@pytest.mark.asyncio
async def test_queue_cancel_after_complete_before_summary_suppresses_completion():
    token = CancellationToken(message_id="message-1")

    async def process_queue(*_args, **_kwargs):
        token.cancel()
        return SimpleNamespace(result=QueueResult.COMPLETED)

    summary = AsyncMock(return_value=("deterministic", None))
    center = _queue_completion_race_center(token, process_queue, summary)

    response = await center._process_room_user_message_locked(
        SimpleNamespace(user_id="user-1", client_request_id="cr-1"),
        "room-1",
        "message-1",
        token=token,
    )

    assert response.success is True
    assert response.error == "Processing cancelled by user"
    summary.assert_not_awaited()
    statuses = [
        call.kwargs["status"] for call in center._emit_processing_status.await_args_list
    ]
    assert statuses == [SSEProcessingStatus.CANCELED]
    center._turn_event_appender.append.assert_not_awaited()
    assert (
        center._emit_processing_status.await_args.kwargs["turn_event_enabled"] is True
    )
    center._persist_turn_completion_kind.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_canceled_root_cas_never_starts_completed_child_task_update():
    token = CancellationToken(message_id="message-1")

    async def process_queue(*_args, **_kwargs):
        return SimpleNamespace(result=QueueResult.COMPLETED)

    center = _queue_completion_race_center(
        token,
        process_queue,
        AsyncMock(return_value=("deterministic", None)),
    )
    child_terminal = AsyncMock()
    center.queue_executor._terminalize_system_task = child_terminal

    async def emit_status(**kwargs):
        if kwargs["status"] == SSEProcessingStatus.COMPLETED:
            token.cancel()
            return None
        return {"accepted": True}

    center._emit_processing_status.side_effect = emit_status
    response = await center._process_room_user_message_locked(
        SimpleNamespace(user_id="user-1", client_request_id="cr-1"),
        "room-1",
        "message-1",
        token=token,
    )

    assert response.error == "Processing cancelled by user"
    child_terminal.assert_not_awaited()
    center._turn_event_appender.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_child_persistence_failure_does_not_block_root_completion():
    token = CancellationToken(message_id="message-1")
    center = _queue_completion_race_center(
        token,
        AsyncMock(return_value=SimpleNamespace(result=QueueResult.COMPLETED)),
        AsyncMock(return_value=("deterministic", None)),
    )
    child_terminal = AsyncMock(side_effect=RuntimeError("child persistence failed"))
    center.queue_executor._terminalize_system_task = child_terminal

    response = await center._process_room_user_message_locked(
        SimpleNamespace(user_id="user-1", client_request_id="cr-1"),
        "room-1",
        "message-1",
        token=token,
    )

    assert response.success is True
    child_terminal.assert_not_awaited()
    assert center._emit_processing_status.await_args.kwargs["status"] == (
        SSEProcessingStatus.COMPLETED
    )
    center._persist_turn_completion_kind.assert_awaited_once_with(
        "message-1",
        "deterministic",
    )
    center._turn_event_appender.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_continuation_child_persistence_failure_does_not_block_root_completion():
    token = CancellationToken(message_id="user-msg-1")
    appender = SimpleNamespace(append=AsyncMock())
    center = _continuation_completion_race_center(
        token,
        AsyncMock(return_value=("deterministic", None)),
        appender,
    )
    child_terminal = AsyncMock(side_effect=RuntimeError("child persistence failed"))
    center.queue_executor._terminalize_system_task = child_terminal

    assert await center._resume_continuation_locked({}, "agent-msg-1", None) is True

    child_terminal.assert_not_awaited()
    assert center._emit_processing_status.await_args.kwargs["status"] == (
        SSEProcessingStatus.COMPLETED
    )
    center._persist_turn_completion_kind.assert_awaited_once_with(
        "user-msg-1",
        "deterministic",
    )
    appender.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_queue_cancel_during_summary_suppresses_turn_completed_and_completed():
    token = CancellationToken(message_id="message-1")
    summary_entered = asyncio.Event()

    async def process_queue(*_args, **_kwargs):
        return SimpleNamespace(result=QueueResult.COMPLETED)

    async def emit_summary(*_args, **_kwargs):
        summary_entered.set()
        await asyncio.Event().wait()

    center = _queue_completion_race_center(token, process_queue, emit_summary)
    processing = asyncio.create_task(
        center._process_room_user_message_locked(
            SimpleNamespace(user_id="user-1", client_request_id="cr-1"),
            "room-1",
            "message-1",
            token=token,
        )
    )
    await summary_entered.wait()
    token.cancel()
    response = await processing

    assert response.error == "Processing cancelled by user"
    statuses = [
        call.kwargs["status"] for call in center._emit_processing_status.await_args_list
    ]
    assert statuses == [SSEProcessingStatus.CANCELED]
    center._turn_event_appender.append.assert_not_awaited()
    assert (
        center._emit_processing_status.await_args.kwargs["turn_event_enabled"] is True
    )
    center._persist_turn_completion_kind.assert_not_awaited()


@pytest.mark.parametrize(
    "details",
    [
        "supervisor data corrupted or incomplete",
        "Supervisor planning failed",
        "Supervisor execution failed unexpectedly",
    ],
)
def test_supervisor_early_failure_uses_durable_cleanup_projection(details):
    root_line = _call_line("_process_supervisor", "_emit_processing_status", details)
    source = inspect.getsource(RoomMessageCenter._process_supervisor)

    assert root_line > 0
    assert "_notify_all_non_terminal_tasks_failed" not in source


def test_supervisor_completed_registers_turn_in_durable_terminal_projection():
    function = _function_node("_handle_supervisor_run_result")
    completed_call = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_emit_processing_status"
        and "SSEProcessingStatus.COMPLETED" in ast.unparse(node)
    )
    assert "turn_event_enabled" in ast.unparse(completed_call)
    assert "system_message_id" in ast.unparse(completed_call)


def test_supervisor_terminal_losers_have_no_imperative_child_cleanup():
    source = inspect.getsource(RoomMessageCenter._handle_supervisor_run_result)

    assert "SSEProcessingStatus.CANCELED" in source
    assert "SSEProcessingStatus.FAILED" in source
    assert "_notify_all_non_terminal_tasks_failed" not in source
    assert "cancel_descendants" not in source
    assert "cancel_agent_messages_by_ids" not in source


def test_v1_resume_summary_completes_before_completed_processing_status():
    _assert_before(
        "_resume_continuation_locked",
        "_emit_unified_summary",
        (),
        ("SSEProcessingStatus.COMPLETED",),
    )


def test_supervisor_terminal_post_loop_side_effects_run_after_root_terminal():
    integration_line = _call_line(
        "_handle_supervisor_run_result",
        "_run_supervisor_terminal_post_loop_integration",
    )
    first_terminal_emit = min(
        _call_line(
            "_handle_supervisor_run_result",
            "_emit_processing_status",
            "SSEProcessingStatus.COMPLETED",
        ),
        _call_line(
            "_handle_supervisor_run_result",
            "_emit_processing_status",
            "SSEProcessingStatus.CANCELED",
        ),
        _call_line(
            "_handle_supervisor_run_result",
            "_emit_processing_status",
            "SSEProcessingStatus.FAILED",
        ),
    )
    assert first_terminal_emit < integration_line
