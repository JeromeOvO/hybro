from __future__ import annotations

from models.orchestration import (
    ActiveDispatchRef,
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
    assert updated.decision_log[-1]["targets"] == []
    assert updated.decision_log[-1]["planner_action"] == action.model_dump(
        mode="json"
    )


def test_record_planner_action_never_exceeds_step_budget():
    from execution.orchestration.run_reducer import record_planner_action

    state = _state(steps_used=8, step_budget=8)
    action = PlannerAction(
        action=PlannerActionType.FAIL,
        reasoning="No safe action remains.",
        failure_reason="Budget exhausted",
    )

    updated = record_planner_action(state, action)

    assert updated.steps_used == 8


def test_record_dispatch_intents_sets_dispatching_and_active_dispatches():
    from execution.orchestration.run_reducer import record_dispatch_intents

    intent = _intent()

    updated = record_dispatch_intents(_state(), [intent])

    assert updated.status == OrchestrationStatus.DISPATCHING
    assert updated.dispatch_intents == [intent]
    assert updated.active_dispatches[0].agent_message_id == "agent-msg-1"
    assert updated.active_dispatches[0].status == "planned"
    assert updated.state_version == 4


def test_record_dispatch_intents_preserves_prior_active_dispatches():
    from execution.orchestration.run_reducer import record_dispatch_intents

    state = _state(
        active_dispatches=[
            ActiveDispatchRef(
                agent_message_id="agent-msg-existing",
                agent_id="agent-2",
                status="awaiting_input",
            )
        ]
    )

    updated = record_dispatch_intents(state, [_intent()])

    assert [
        (dispatch.agent_message_id, dispatch.status)
        for dispatch in updated.active_dispatches
    ] == [
        ("agent-msg-existing", "awaiting_input"),
        ("agent-msg-1", "planned"),
    ]


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


def test_record_step_result_clears_matched_dispatch_without_result_message_id():
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
    )

    updated = record_step_result_metadata(
        state,
        result,
        status=OrchestrationStatus.RUNNING,
        matched_intent_id="intent-1",
        advance_step=False,
    )

    assert updated.dispatch_intents[0].status == "success"
    assert updated.active_dispatches == []


def test_record_hitl_no_progress_adds_canonical_fact_and_replan_signal():
    from execution.orchestration.run_reducer import record_hitl_resolution

    state = _state(
        status=OrchestrationStatus.AWAITING_USER,
        pending_hitl_request_ids=["hitl-1"],
        open_questions=[
            {
                "request_id": "hitl-1",
                "source": "agent",
                "status": "open",
                "prompt": "Provide the submission.",
            }
        ],
    )

    updated = record_hitl_resolution(
        state,
        request_id="hitl-1",
        response='{"client":{"name":"Acme"}}',
        hitl_result={
            "source": "agent",
            "agent_id": "agent-1",
            "agent_name": "Insurer",
            "agent_no_progress": True,
            "agent_no_progress_code": "agent_repeated_input_required",
            "response_text": "Provide the submission.",
        },
    )

    assert updated.status == OrchestrationStatus.RUNNING
    assert updated.pending_hitl_request_ids == []
    assert updated.facts[-1]["source"] == "hitl_user_reply"
    assert updated.facts[-1]["text"] == '{"client":{"name":"Acme"}}'
    assert updated.decision_log[-1]["code"] == "agent_repeated_input_required"
