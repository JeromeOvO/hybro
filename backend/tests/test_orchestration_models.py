import pytest

from execution.orchestration.run_reducer import (
    OrchestrationTransitionError,
    mark_running,
    mark_terminal,
)
from models.orchestration import (
    OrchestrationEventType,
    OrchestrationRunState,
    OrchestrationStatus,
    PlannerAction,
)


def _run_state(**overrides):
    values = {
        "run_id": "run-1",
        "room_id": "room-1",
        "user_message_id": "msg-1",
        "goal": "Get quotes",
        "candidate_agent_ids": ["broker", "insurer"],
        "client_request_id": "cr-1",
    }
    values.update(overrides)
    return OrchestrationRunState(**values)


def test_run_state_defaults_are_v2_and_non_terminal():
    state = _run_state()

    assert state.schema_version == 2
    assert state.status == OrchestrationStatus.CREATED
    assert state.state_version == 0
    assert state.steps_used == 0
    assert state.candidate_agent_ids == ["broker", "insurer"]


def test_event_types_cover_recovery_and_terminal_projection():
    assert OrchestrationEventType.RUN_RECOVERED.value == "run_recovered"
    assert OrchestrationEventType.PUBLIC_LIFECYCLE_PROJECTED.value == (
        "public_lifecycle_projected"
    )


def test_planner_action_schema_rejects_unknown_actions():
    with pytest.raises(ValueError, match="action"):
        PlannerAction(
            action="done",
            reasoning="legacy terminal",
        )


def test_mark_running_returns_updated_copy_without_mutating_input():
    state = _run_state(state_version=3)
    original_updated_at = state.updated_at

    updated = mark_running(state)

    assert updated is not state
    assert state.status == OrchestrationStatus.CREATED
    assert state.state_version == 3
    assert state.updated_at == original_updated_at
    assert updated.status == OrchestrationStatus.RUNNING
    assert updated.state_version == 4
    assert updated.updated_at > original_updated_at


def test_mark_running_rejects_already_terminal_state():
    state = _run_state(status=OrchestrationStatus.COMPLETED)

    with pytest.raises(OrchestrationTransitionError):
        mark_running(state)


def test_mark_terminal_sets_terminal_status_and_reason_without_mutating_input():
    state = _run_state(state_version=2)
    original_updated_at = state.updated_at

    updated = mark_terminal(
        state,
        OrchestrationStatus.FAILED,
        reason="planner failed",
    )

    assert updated is not state
    assert state.status == OrchestrationStatus.CREATED
    assert state.terminal_reason is None
    assert state.state_version == 2
    assert state.updated_at == original_updated_at
    assert updated.status == OrchestrationStatus.FAILED
    assert updated.terminal_reason == "planner failed"
    assert updated.state_version == 3
    assert updated.updated_at > original_updated_at


def test_mark_terminal_rejects_non_terminal_target_status():
    state = _run_state()

    with pytest.raises(OrchestrationTransitionError):
        mark_terminal(state, OrchestrationStatus.RUNNING, reason="not terminal")


def test_mark_terminal_rejects_rewriting_already_terminal_state():
    state = _run_state(
        status=OrchestrationStatus.COMPLETED,
        terminal_reason="done",
        state_version=4,
    )

    with pytest.raises(OrchestrationTransitionError):
        mark_terminal(state, OrchestrationStatus.FAILED, reason="rewrite")


def test_mark_terminal_coerces_raw_string_status_to_enum():
    state = _run_state()

    updated = mark_terminal(state, "completed", reason="done")

    assert updated.status == OrchestrationStatus.COMPLETED
    assert isinstance(updated.status, OrchestrationStatus)
    assert updated.terminal_reason == "done"
