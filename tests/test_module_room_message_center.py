"""
Unit tests for RoomMessageCenter module.

Tests cover:
- _validate_room_message_request: input validation
- _find_paused_agent: trajectory search
- _extract_clarify_question: clarification extraction
- _append_paused_result_to_trajectory: in-place mutation
"""

import pytest
from unittest.mock import MagicMock

from modules.RoomMessageCenter import RoomMessageCenter
from models.supervisor_v2 import (
    SupervisorTrajectory,
    TrajectoryEntry,
    SupervisorAction,
    ActionType,
    V2StepResult,
    StepStatus,
)


# =============================================================================
# _validate_room_message_request Tests
# =============================================================================


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
            V2StepResult(
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
