from __future__ import annotations

from models.orchestration import (
    DispatchIntent,
    OrchestrationRunState,
    OrchestrationStatus,
    PlannerAction,
    PlannerActionType,
    PlannerQuestion,
)
from models.supervisor import StepResult, StepStatus


def _state(**overrides) -> OrchestrationRunState:
    values = {
        "run_id": "run-1",
        "room_id": "room-1",
        "user_message_id": "user-msg-1",
        "goal": "Coordinate the selected agents",
        "candidate_agent_ids": ["agent-1"],
        "status": OrchestrationStatus.RUNNING,
        "state_version": 3,
    }
    values.update(overrides)
    return OrchestrationRunState(**values)


def _intent(status: str = "planned") -> DispatchIntent:
    return DispatchIntent(
        step_id="run-1:step-1",
        step_target_id="run-1:step-1:target-1",
        dispatch_intent_id="intent-1",
        planned_agent_message_id="agent-msg-1",
        agent_id="agent-1",
        task="Review the submission",
        task_hash="hash-1",
        status=status,
    )


def test_record_planner_action_increments_step_and_logs_decision():
    from execution.orchestration.run_reducer import record_planner_action

    state = _state(steps_used=1)
    action = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="Need the applicant revenue.",
        questions=[PlannerQuestion(prompt="What is annual revenue?")],
    )

    updated = record_planner_action(state, action)

    assert updated is not state
    assert updated.steps_used == 2
    assert updated.state_version == state.state_version + 1
    assert updated.updated_at >= state.updated_at
    assert updated.last_planner_action is not None
    assert updated.last_planner_action.action == "ask_user"
    assert updated.decision_log[-1]["reasoning"] == "Need the applicant revenue."


def test_record_dispatch_intents_sets_dispatching_and_active_dispatches():
    from execution.orchestration.run_reducer import record_dispatch_intents

    intent = _intent()

    updated = record_dispatch_intents(_state(), [intent])

    assert updated.status == OrchestrationStatus.DISPATCHING
    assert updated.dispatch_intents == [intent]
    assert updated.active_dispatches[0].agent_message_id == "agent-msg-1"
    assert updated.active_dispatches[0].status == "planned"
    assert updated.state_version == 4


def test_record_step_result_updates_intent_and_active_dispatch_status():
    from execution.orchestration.run_reducer import (
        record_dispatch_intents,
        record_step_result_metadata,
    )

    state = record_dispatch_intents(_state(), [_intent()])
    result = StepResult(
        step_number=1,
        agent_id="agent-1",
        agent_name="Agent One",
        task="Review the submission",
        response_text="Done",
        success=True,
        status=StepStatus.SUCCESS,
        agent_message_id="agent-msg-1",
    )

    updated = record_step_result_metadata(
        state,
        result,
        status=OrchestrationStatus.RUNNING,
        matched_intent_id="intent-1",
        advance_step=False,
    )

    assert updated.status == OrchestrationStatus.RUNNING
    assert updated.dispatch_intents[0].status == "success"
    assert updated.active_dispatches == []
    assert updated.state_version == state.state_version + 1
