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
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.types import TaskState

from common.a2a_constants import CommonTaskState, SSEProcessingStatus
from common.dto import MessageCommitted
from execution.orchestration.room_message_center import RoomMessageCenter
from models.hitl import InterruptKind
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

# =============================================================================
# _validate_room_message_request Tests
# =============================================================================


class RecordingEventPublisher:
    def __init__(self):
        self.internal_events = []

    async def emit_internal(
        self,
        event,
        *,
        wait_for_local_handlers: bool = False,
        broadcast: bool = True,
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
        rmc._room_facade = None
        rmc._room_bound = False

        with pytest.raises(
            RuntimeError,
            match=r"RoomMessageCenter\.bind_facade\(\) not called - startup incomplete",
        ):
            rmc._require_room_facade()

    def test_bind_facade_makes_room_persistence_available(self):
        rmc = RoomMessageCenter.__new__(RoomMessageCenter)
        facade = MagicMock()

        rmc.bind_facade(facade)

        assert rmc._require_room_facade() is facade


@pytest.mark.asyncio
async def test_process_room_user_message_cancelled_error_emits_canceled_and_reraises():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
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
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc._emit_processing_status = AsyncMock()
    rmc.delivery = SimpleNamespace(clear_cancellation=MagicMock())
    rmc._turn_event_appender = SimpleNamespace(append=AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        await rmc.process_room_user_message(request)

    rmc._turn_event_appender.append.assert_awaited_once_with(
        "room-1", "user-msg-1", "turn_canceled", {}
    )
    rmc._notify_all_non_terminal_tasks_failed.assert_awaited_once_with(
        "room-1", "user-msg-1"
    )
    rmc._emit_processing_status.assert_awaited_once_with(
        room_id="room-1",
        status=SSEProcessingStatus.CANCELED,
        message_id="user-msg-1",
        lifecycle_message_id="user-msg-1",
    )
    rmc.delivery.clear_cancellation.assert_called_once_with("user-msg-1")
    rmc._release_room_lock.assert_awaited_once_with(
        "room-1", "owner-1", acquired_at=pytest.approx(rmc._release_room_lock.call_args.kwargs["acquired_at"])
    )


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
            targets=[{"agent_id": agent_id, "agent_name": agent_name, "task": "do stuff"}],
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


class TestFindPausedAgent:
    def test_finds_paused_agent(self):
        t = _make_trajectory_with_paused("a1", "Alpha", "msg-p1")
        aid, aname = RoomMessageCenter._find_paused_agent(t, "msg-p1")
        assert aid == "a1"
        assert aname == "Alpha"

    def test_returns_none_when_not_found(self):
        t = _make_trajectory_with_paused("a1", "Alpha", "msg-p1")
        aid, aname = RoomMessageCenter._find_paused_agent(t, "msg-other")
        assert aid is None
        assert aname is None

    def test_returns_none_on_empty_trajectory(self):
        t = SupervisorTrajectory()
        aid, aname = RoomMessageCenter._find_paused_agent(t, "msg-p1")
        assert aid is None
        assert aname is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interrupt_kind",
    [InterruptKind.PUSH_NOTIFICATION, InterruptKind.HITL_AGENT],
)
async def test_resume_supervisor_agent_interrupt_publishes_agent_message_committed(
    interrupt_kind,
):
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.event_publisher = RecordingEventPublisher()
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=None)
    )
    rmc.memory_reader = SimpleNamespace(
        get_room_memory_by_room_id=AsyncMock(return_value=None)
    )
    rmc.context_memory_runtime = None
    rmc.room_reader = SimpleNamespace(
        get_room_by_room_id=AsyncMock(
            return_value=SimpleNamespace(
                room_agent_set={"a1": "Alpha"},
                extend_info={},
            )
        )
    )
    rmc.agent_lookup = SimpleNamespace(
        get_agent_by_agent_id=AsyncMock(return_value=None)
    )
    token = SimpleNamespace(is_cancelled=False)
    rmc.delivery = SimpleNamespace(
        get_token=MagicMock(return_value=None),
        create_token=MagicMock(return_value=token),
    )

    async def run_supervisor(**kwargs):
        return SupervisorRunResult(
            status=RunStatus.COMPLETED,
            trajectory=kwargs["resumed_trajectory"],
        )

    rmc.supervisor_executor = SimpleNamespace(run=AsyncMock(side_effect=run_supervisor))
    rmc._handle_supervisor_run_result = AsyncMock()
    rmc._log_room_memory_stats = AsyncMock()
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc._emit_processing_status = AsyncMock()
    rmc._turn_event_appender = None

    trajectory = _make_trajectory_with_paused("a1", "Alpha", "paused-msg")
    continuation = {
        "room_id": "room-1",
        "user_message_id": "umsg-1",
        "message_text": "original user task",
        "interrupt_kind": interrupt_kind.value,
        "trajectory": trajectory.model_dump(mode="json"),
        "room_config": {},
        "agent_registry": [],
    }

    status = await rmc._resume_supervisor(
        continuation,
        paused_message_id="paused-msg",
        task_result_text="agent result",
    )

    assert status == RunStatus.COMPLETED
    committed_events = [
        event
        for event in rmc.event_publisher.internal_events
        if isinstance(event, MessageCommitted)
    ]
    assert len(committed_events) == 1
    event = committed_events[0]
    assert event.room_id == "room-1"
    assert event.message_id == "paused-msg"
    assert event.message_type == "agent"
    assert event.agent_id == "a1"
    assert event.agent_name == "Alpha"
    assert event.was_successful is True


# =============================================================================
# _extract_clarify_question Tests
# =============================================================================


class TestExtractClarifyQuestion:
    def test_extracts_clarify_question(self):
        from datetime import datetime
        entry = TrajectoryEntry(
            step_number=1,
            action=SupervisorAction(
                action=ActionType.CLARIFY,
                reasoning="Need more info",
                targets=[],
                clarification_question="What do you mean?",
            ),
            results=[],
            started_at=datetime(2026, 1, 1),
        )
        t = SupervisorTrajectory()
        t.entries = [entry]
        assert RoomMessageCenter._extract_clarify_question(t) == "What do you mean?"

    def test_returns_none_when_no_clarify(self):
        from datetime import datetime
        entry = TrajectoryEntry(
            step_number=1,
            action=SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="Go",
                targets=[{"agent_id": "a1", "agent_name": "Alpha", "task": "x"}],
            ),
            results=[],
            started_at=datetime(2026, 1, 1),
        )
        t = SupervisorTrajectory()
        t.entries = [entry]
        assert RoomMessageCenter._extract_clarify_question(t) is None

    def test_returns_none_on_empty_trajectory(self):
        t = SupervisorTrajectory()
        assert RoomMessageCenter._extract_clarify_question(t) is None

    def test_returns_last_clarify_when_multiple(self):
        from datetime import datetime
        e1 = TrajectoryEntry(
            step_number=1,
            action=SupervisorAction(
                action=ActionType.CLARIFY,
                reasoning="First",
                targets=[],
                clarification_question="First question?",
            ),
            results=[],
            started_at=datetime(2026, 1, 1),
        )
        e2 = TrajectoryEntry(
            step_number=2,
            action=SupervisorAction(
                action=ActionType.CLARIFY,
                reasoning="Second",
                targets=[],
                clarification_question="Second question?",
            ),
            results=[],
            started_at=datetime(2026, 1, 1),
        )
        t = SupervisorTrajectory()
        t.entries = [e1, e2]
        assert RoomMessageCenter._extract_clarify_question(t) == "Second question?"


# =============================================================================
# _append_paused_result_to_trajectory Tests
# =============================================================================


class TestAppendPausedResult:
    def test_replaces_paused_result_with_success(self):
        t = _make_trajectory_with_paused("a1", "Alpha", "msg-p1")
        RoomMessageCenter._append_paused_result_to_trajectory(
            t, "msg-p1", "Agent completed the task"
        )
        result = t.entries[0].results[0]
        assert result.status == StepStatus.SUCCESS
        assert result.response_text == "Agent completed the task"
        assert result.success is True
        assert result.error_message is None

    def test_replaces_paused_result_with_failure_when_no_text(self):
        t = _make_trajectory_with_paused("a1", "Alpha", "msg-p1")
        RoomMessageCenter._append_paused_result_to_trajectory(
            t, "msg-p1", None
        )
        result = t.entries[0].results[0]
        assert result.status == StepStatus.FAILED
        assert result.success is False
        assert result.error_message is not None

    def test_no_change_when_message_id_not_found(self):
        t = _make_trajectory_with_paused("a1", "Alpha", "msg-p1")
        RoomMessageCenter._append_paused_result_to_trajectory(
            t, "msg-other", "text"
        )
        assert t.entries[0].results[0].status == StepStatus.PAUSED


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
            if isinstance(body, list) and all(isinstance(item, ast.stmt) for item in body):
                bodies.append(body)
        if isinstance(node, ast.Try):
            bodies.extend(handler.body for handler in node.handlers)
        if isinstance(node, ast.Match):
            bodies.extend(case.body for case in node.cases)
        for body in bodies:
            if any(item is statement for item in body):
                return body
    raise AssertionError(f"body containing statement at line {statement.lineno} not found")


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
async def test_failed_room_lock_still_emits_terminal_status_when_task_transition_fails():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
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
    rmc.tsm.transition_task.assert_awaited_once_with(
        agent_message,
        CommonTaskState.FAILED,
        error="Processing failed",
    )
    emit.assert_awaited_once()
    rmc.delivery.send_processing_status.assert_not_awaited()


def test_failed_room_lock_notifies_non_terminal_tasks_before_failed_processing_status():
    _assert_before(
        "process_room_user_message",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("Room is busy processing another message",),
    )


def test_supervisor_prep_missing_notifies_before_failed_processing_status():
    _assert_before(
        "_process_room_user_message_locked",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("Supervisor-enabled room missing supervisor preparation data",),
    )


def test_queue_canceled_side_effects_complete_before_canceled_processing_status():
    queue_path = _ROOT / "execution" / "orchestration" / "queue_executor.py"
    tree = ast.parse(queue_path.read_text(), filename=str(queue_path))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "process_queue"
    )
    send_call = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_emit_processing_status"
        and "sse_status" in ast.unparse(node)
    )
    emit_statement = _statement_containing_call(function, send_call)
    candidates = [
        (node.lineno, ast.unparse(node))
        for statement in _preceding_path_statements(function, emit_statement)
        for node in _path_calls(statement)
        if isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and "turn_canceled" in ast.unparse(node)
    ]
    assert candidates, "turn_canceled append not found in deferred SSE path"
    assert min(line for line, _ in candidates) < send_call.lineno


def test_queue_failure_appends_turn_failed_and_notifies_before_failed_processing_status():
    _assert_before(
        "_process_room_user_message_locked",
        "append",
        ("turn_failed",),
        ("Failed to process agent messages",),
    )
    _assert_before(
        "_process_room_user_message_locked",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("Failed to process agent messages",),
    )


def test_v1_resume_failure_notifies_non_terminal_tasks_before_terminal_emit():
    resume_line = _call_line(
        "_resume_continuation_locked",
        "resume_from_continuation",
        "before_terminal_failure=self._notify_all_non_terminal_tasks_failed",
    )
    assert resume_line > 0


def test_root_queue_completion_appends_turn_completed_before_completed_processing_status():
    _assert_before(
        "_process_room_user_message_locked",
        "append",
        ("turn_completed",),
        ("SSEProcessingStatus.COMPLETED",),
    )


def test_supervisor_corrupted_data_notifies_before_failed_processing_status():
    _assert_before(
        "_process_supervisor",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("supervisor data corrupted or incomplete",),
    )


def test_supervisor_clarify_resume_failed_has_no_required_post_emit_side_effects():
    fn = _function_node("_process_supervisor")
    send_line = _call_line(
        "_process_supervisor",
        "_emit_processing_status",
        "Clarify resume failed",
    )
    return_line = min(
        node.lineno
        for node in ast.walk(fn)
        if isinstance(node, ast.Return) and node.lineno > send_line
    )
    blocking_calls = {
        "update_room_by_room_id",
        "update_room_user_message_by_message_id",
        "_notify_all_non_terminal_tasks_failed",
        "_persist_failed_trajectory",
        "emit_synthesis_message",
    }
    post_emit_calls = []
    for node in ast.walk(fn):
        if (
            not isinstance(node, ast.Call)
            or node.lineno <= send_line
            or node.lineno >= return_line
        ):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if name in blocking_calls:
            post_emit_calls.append((node.lineno, ast.unparse(node)))
    assert not post_emit_calls


def test_supervisor_planning_failure_notifies_before_failed_processing_status():
    _assert_before(
        "_process_supervisor",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("Supervisor planning failed",),
    )


def test_supervisor_execution_failure_notifies_before_failed_processing_status():
    _assert_before(
        "_process_supervisor",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("Supervisor execution failed unexpectedly",),
    )


def test_supervisor_resume_deserialization_failure_notifies_before_failed_status():
    _assert_before(
        "_resume_supervisor",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("Supervisor resume: corrupted trajectory data",),
    )


def test_supervisor_resume_room_lookup_failure_notifies_before_failed_status():
    _assert_before(
        "_resume_supervisor",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("Supervisor resume: room not found",),
    )


def test_supervisor_resume_executor_failure_notifies_before_failed_status():
    _assert_before(
        "_resume_supervisor",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("Supervisor resume: executor failed",),
    )


def test_supervisor_resume_canceled_appends_and_notifies_before_canceled_status():
    _assert_before(
        "_resume_supervisor",
        "append",
        ("turn_canceled",),
        ("SSEProcessingStatus.CANCELED",),
    )
    _assert_before(
        "_resume_supervisor",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("SSEProcessingStatus.CANCELED",),
    )


def test_supervisor_completed_appends_turn_completed_before_completed_processing_status():
    _assert_before(
        "_handle_supervisor_run_result",
        "append",
        ("turn_completed",),
        ("SSEProcessingStatus.COMPLETED",),
    )


def test_supervisor_canceled_appends_and_notifies_before_terminal_status():
    _assert_before(
        "_handle_supervisor_run_result",
        "append",
        ("turn_canceled",),
        ("SSEProcessingStatus.CANCELED",),
    )
    _assert_before(
        "_handle_supervisor_run_result",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("SSEProcessingStatus.CANCELED",),
    )


def test_supervisor_failed_appends_and_notifies_before_terminal_status():
    _assert_before(
        "_handle_supervisor_run_result",
        "append",
        ("turn_failed",),
        ("supervisor execution failed",),
    )
    _assert_before(
        "_handle_supervisor_run_result",
        "_notify_all_non_terminal_tasks_failed",
        (),
        ("supervisor execution failed",),
    )


def test_v1_resume_completion_side_effects_complete_before_completed_processing_status():
    _assert_before(
        "_resume_continuation_locked",
        "_emit_unified_summary",
        (),
        ("SSEProcessingStatus.COMPLETED",),
    )
    _assert_before(
        "_resume_continuation_locked",
        "append",
        ("turn_completed",),
        ("SSEProcessingStatus.COMPLETED",),
    )


def test_supervisor_terminal_post_loop_side_effects_complete_before_terminal_status_or_are_best_effort():
    integration_line = _call_line(
        "_handle_supervisor_run_result",
        "_run_supervisor_terminal_post_loop_integration",
    )
    first_terminal_emit = min(
        _call_line("_handle_supervisor_run_result", "_emit_processing_status", "SSEProcessingStatus.COMPLETED"),
        _call_line("_handle_supervisor_run_result", "_emit_processing_status", "SSEProcessingStatus.CANCELED"),
        _call_line("_handle_supervisor_run_result", "_emit_processing_status", "supervisor execution failed"),
    )
    assert integration_line < first_terminal_emit


def test_clarifying_soft_complete_appends_turn_completed_before_frontend_completed_status():
    _assert_before(
        "_handle_supervisor_run_result",
        "append",
        ("turn_completed",),
        ("SSEProcessingStatus.COMPLETED",),
        emit_occurrence=2,
    )

@pytest.mark.asyncio
async def test_supervisor_resume_race_condition():
    """
    Tests that resume_queue_from_continuation correctly handles concurrent resumes
    for the same room by locking before doing a destructive read, avoiding stale trajectory state.
    """
    from datetime import datetime, timezone
    from models.supervisor import DelegateTarget

    continuation_store = AsyncMock()
    now = datetime.now(timezone.utc)
    trajectory = SupervisorTrajectory(
        room_id="room-1",
        entries=[
            TrajectoryEntry(
                step_number=1,
                started_at=now,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="go",
                    targets=[
                        DelegateTarget(agent_id="a1", agent_name="A1", task="t"),
                        DelegateTarget(agent_id="a2", agent_name="A2", task="t"),
                    ]
                ),
                results=[
                    StepResult(step_number=1, agent_id="a1", agent_name="A1", task="t", response_text="", status=StepStatus.PAUSED, agent_message_id="msg-1", paused_message_id="msg-1"),
                    StepResult(step_number=1, agent_id="a2", agent_name="A2", task="t", response_text="", status=StepStatus.PAUSED, agent_message_id="msg-2", paused_message_id="msg-2"),
                ]
            )
        ]
    )

    # Initial state shared by both
    continuation_state = {
        "room_id": "room-1",
        "supervisor": True,
        "agent_registry": [],
        "room_config": {},
        "trajectory": trajectory.model_dump(mode="json")
    }

    # Simulate get_pending_continuation_on_message being called concurrently and returning the same state.
    async def mock_get_pending(msg_id):
        await asyncio.sleep(0.01) # force yield
        return continuation_state.copy()
        
    continuation_store.get_pending_continuation_on_message.side_effect = mock_get_pending
    
    # Track when get_and_clear is called to verify they get sequential, updated state
    cleared_msgs = set()
    async def mock_get_and_clear(msg_id):
        if msg_id in cleared_msgs:
            return None
        cleared_msgs.add(msg_id)
        return continuation_state.copy()

    continuation_store.get_and_clear_continuation_on_message.side_effect = mock_get_and_clear
    continuation_store.get_and_clear_continuation_on_user_message.return_value = None

    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    rmc.continuation_store = continuation_store
    
    # Real asyncio lock for the room
    room_locks = {}
    async def mock_acquire(room_id):
        if room_id not in room_locks:
            room_locks[room_id] = asyncio.Lock()
        await room_locks[room_id].acquire()
        return "owner"
    
    async def mock_release(room_id, owner, acquired_at=None):
        room_locks[room_id].release()
        
    rmc._acquire_room_lock = AsyncMock(side_effect=mock_acquire)
    rmc._release_room_lock = AsyncMock(side_effect=mock_release)

    async def mock_resume_supervisor(continuation, msg_id, result_text):
        traj = SupervisorTrajectory.model_validate(continuation["trajectory"])
        # Complete the agent that resumed
        for entry in traj.entries:
            for res in entry.results:
                if res.agent_message_id == msg_id:
                    res.status = StepStatus.SUCCESS
        
        # Save mutated state back so the next lock owner sees it
        continuation_state["trajectory"] = traj.model_dump(mode="json")
        return RunStatus.PAUSED if any(r.status == StepStatus.PAUSED for e in traj.entries for r in e.results) else RunStatus.COMPLETED

    rmc._resume_supervisor = AsyncMock(side_effect=mock_resume_supervisor)

    # Run both concurrently
    results = await asyncio.gather(
        rmc.resume_queue_from_continuation("msg-1", "result 1"),
        rmc.resume_queue_from_continuation("msg-2", "result 2"),
    )
    
    # Both should return successfully
    assert all(results)
    
    # Both messages should have been processed
    assert len(cleared_msgs) == 2
    
    # The final state in continuation_state should have both agents SUCCESS
    final_traj = SupervisorTrajectory.model_validate(continuation_state["trajectory"])
    assert all(r.status == StepStatus.SUCCESS for e in final_traj.entries for r in e.results)
