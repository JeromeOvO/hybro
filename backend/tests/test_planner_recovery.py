import pytest

from execution.orchestration.action_validator import PlannerActionValidationError
from execution.orchestration.planner_recovery import (
    planner_validation_fingerprint,
    record_recoverable_planner_rejection,
    resolve_open_planner_validation_failures,
)
from models.orchestration import OrchestrationRunState, PlannerAction, PlannerActionType


def _state() -> OrchestrationRunState:
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Coordinate",
        candidate_agent_ids=["agent-1"],
        step_budget=8,
        steps_used=0,
    )


def test_planner_validation_fingerprint_uses_action_shape_not_reasoning():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="This reasoning can vary.",
        targets=[],
    )

    fingerprint = planner_validation_fingerprint(
        error_code="planner_output_invalid",
        stage="adapter",
        planner_action=action,
    )

    assert fingerprint == (
        "planner_validator:adapter:planner_output_invalid:delegate:targets=0:refs=none"
    )


def test_recoverable_planner_rejection_consumes_step_and_records_failure():
    state = _state()

    failure, exhausted = record_recoverable_planner_rejection(
        state,
        error_code="planner_output_invalid",
        error_message="planner adapter expected a JSON object",
        planner_action=None,
        stage="adapter",
    )

    assert exhausted is False
    assert state.steps_used == 1
    assert state.open_failures == [failure]
    assert failure.source == "planner_validator"
    assert failure.error_code == "planner_output_invalid"
    assert failure.retry_count == 0
    assert failure.recoverable is True


def test_repeated_planner_rejection_exhausts_matching_fingerprint():
    state = _state()
    first, exhausted = record_recoverable_planner_rejection(
        state,
        error_code="planner_output_invalid",
        error_message="bad output",
        planner_action=None,
        stage="adapter",
        max_retries=1,
    )
    assert exhausted is False

    second, exhausted = record_recoverable_planner_rejection(
        state,
        error_code="planner_output_invalid",
        error_message="bad output again",
        planner_action=None,
        stage="adapter",
        max_retries=1,
    )

    assert first.failure_id == second.failure_id
    assert second.retry_count == 1
    assert second.status == "abandoned"
    assert exhausted is True
    assert state.steps_used == 2


def test_valid_action_resolves_open_planner_validation_failures():
    state = _state()
    failure, _ = record_recoverable_planner_rejection(
        state,
        error_code="planner_output_invalid",
        error_message="bad output",
        planner_action=None,
        stage="adapter",
    )

    resolve_open_planner_validation_failures(state)

    assert failure.status == "resolved"


def test_recovery_validation_keeps_open_failures_when_complete_rejected():
    from types import SimpleNamespace

    from execution.orchestration.supervisor_executor import SupervisorExecutor
    from models.orchestration import ActiveDispatchRef, PlannerAction, PlannerActionType

    state = _state()
    failure, _ = record_recoverable_planner_rejection(
        state,
        error_code="planner_output_invalid",
        error_message="bad output",
        planner_action=None,
        stage="adapter",
    )
    state.active_dispatches = [
        ActiveDispatchRef(
            agent_message_id="msg-active",
            agent_id="agent-1",
            status="working",
        )
    ]
    recovery = PlannerAction(
        action=PlannerActionType.COMPLETE,
        reasoning="Recover to complete",
    )
    executor = SupervisorExecutor(
        supervisor_service=SimpleNamespace(),
        room_runtime=SimpleNamespace(),
        tsm=SimpleNamespace(),
        delivery=SimpleNamespace(),
        message_reader=SimpleNamespace(),
        message_writer=SimpleNamespace(),
        task_state_store=SimpleNamespace(),
        continuation_store=SimpleNamespace(),
        internal_event_publisher=SimpleNamespace(),
        agent_dispatcher=SimpleNamespace(),
        agent_message_processor=SimpleNamespace(),
    )

    with pytest.raises(PlannerActionValidationError):
        executor._validate_recovery_planner_action(
            state,
            recovery,
            resource_fingerprints={},
        )

    assert failure.status == "open"
