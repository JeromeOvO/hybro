import pytest

from execution.orchestration.action_validator import (
    PlannerActionValidationError,
    PlannerActionValidator,
)
from execution.orchestration.room_supervisor_service import RoomSupervisorService
from models.orchestration import (
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
)


def _target(agent_id: str = "agent-1", task: str = "do work"):
    return PlannedDelegateTarget(
        agent_id=agent_id,
        agent_name=f"Agent {agent_id}",
        task=task,
    )


def _action(
    action_type: PlannerActionType,
    *,
    targets: list[PlannedDelegateTarget] | None = None,
):
    return PlannerAction(
        action=action_type,
        reasoning="test",
        targets=targets or [],
    )


def _validate(
    action: PlannerAction,
    *,
    candidate_agent_ids: list[str] | None = None,
    steps_used: int = 0,
    step_budget: int = 8,
    has_agent_output: bool = False,
):
    return PlannerActionValidator.validate(
        action,
        candidate_agent_ids=candidate_agent_ids or ["agent-1", "agent-2"],
        steps_used=steps_used,
        step_budget=step_budget,
        has_agent_output=has_agent_output,
    )


def test_valid_delegate_returns_action_unchanged():
    action = _action(PlannerActionType.DELEGATE, targets=[_target()])

    result = _validate(action)

    assert result is action


def test_validate_can_be_called_with_default_context_for_valid_action():
    action = PlannerAction(
        action=PlannerActionType.FAIL,
        reasoning="cannot proceed",
        failure_reason="no viable plan",
    )

    result = PlannerActionValidator.validate(action)

    assert result is action


def test_validate_default_context_raises_domain_error_for_delegate():
    action = _action(PlannerActionType.DELEGATE, targets=[_target()])

    with pytest.raises(PlannerActionValidationError, match="candidate"):
        PlannerActionValidator.validate(action)


def test_out_of_scope_delegate_target_is_rejected():
    action = _action(
        PlannerActionType.DELEGATE,
        targets=[_target(agent_id="outside-agent")],
    )

    with pytest.raises(PlannerActionValidationError, match="candidate"):
        _validate(action, candidate_agent_ids=["agent-1"])


def test_terminal_synthesis_allowed_after_agent_output():
    action = _action(PlannerActionType.SYNTHESIZE)

    result = _validate(action, has_agent_output=True)

    assert result is action


def test_budget_exhaustion_rejects_delegate_and_complete():
    delegate = _action(PlannerActionType.DELEGATE, targets=[_target()])
    complete = _action(PlannerActionType.COMPLETE)

    with pytest.raises(PlannerActionValidationError, match="step budget"):
        _validate(delegate, steps_used=8, step_budget=8)

    with pytest.raises(PlannerActionValidationError, match="step budget"):
        _validate(
            complete,
            steps_used=8,
            step_budget=8,
            has_agent_output=True,
        )


def test_budget_exhaustion_allows_synthesize_and_fail():
    synthesize = _action(PlannerActionType.SYNTHESIZE)
    fail = PlannerAction(
        action=PlannerActionType.FAIL,
        reasoning="cannot continue",
        failure_reason="step budget exhausted",
    )

    assert (
        _validate(
            synthesize,
            steps_used=8,
            step_budget=8,
            has_agent_output=True,
        )
        is synthesize
    )
    assert _validate(fail, steps_used=8, step_budget=8) is fail


def test_delegate_rejects_empty_targets():
    action = _action(PlannerActionType.DELEGATE)

    with pytest.raises(PlannerActionValidationError, match="target"):
        _validate(action)


def test_delegate_rejects_empty_target_task():
    action = _action(
        PlannerActionType.DELEGATE,
        targets=[_target(task="  ")],
    )

    with pytest.raises(PlannerActionValidationError, match="task"):
        _validate(action)


@pytest.mark.parametrize(
    "action_type",
    [PlannerActionType.COMPLETE, PlannerActionType.SYNTHESIZE],
)
def test_complete_and_synthesize_before_agent_output_are_rejected(action_type):
    action = _action(action_type)

    with pytest.raises(PlannerActionValidationError, match="agent output"):
        _validate(action, has_agent_output=False)


def test_complete_allowed_after_agent_output_before_budget_exhaustion():
    action = _action(PlannerActionType.COMPLETE)

    result = _validate(action, has_agent_output=True)

    assert result is action


@pytest.mark.parametrize(
    ("legacy_action", "planner_action"),
    [
        ("clarify", PlannerActionType.ASK_USER),
        ("done", PlannerActionType.COMPLETE),
        ("delegate", PlannerActionType.DELEGATE),
        ("synthesize", PlannerActionType.SYNTHESIZE),
    ],
)
def test_v2_adapter_maps_legacy_action_names(legacy_action, planner_action):
    action = RoomSupervisorService._parse_legacy_action_as_planner_action(
        {
            "action": legacy_action,
            "reasoning": "test",
            "targets": [
                {
                    "agent_id": "agent-1",
                    "agent_name": "Agent One",
                    "task": "do work",
                }
            ],
            "questions": [
                {
                    "prompt": "What do you need?",
                    "prompt_type": "text",
                }
            ],
            "synthesis_instruction": "combine the answers",
        }
    )

    assert action.action == planner_action
    assert action.reasoning == "test"
    assert action.targets[0].agent_id == "agent-1"
    assert action.targets[0].agent_name == "Agent One"
    assert action.targets[0].task == "do work"


def test_v2_adapter_unknown_action_raises():
    with pytest.raises(ValueError, match="unknown planner action"):
        RoomSupervisorService._parse_legacy_action_as_planner_action(
            {
                "action": "mystery",
                "reasoning": "test",
            }
        )


def test_v2_adapter_missing_action_raises():
    with pytest.raises(ValueError, match="action"):
        RoomSupervisorService._parse_legacy_action_as_planner_action(
            {
                "reasoning": "test",
            }
        )


def test_v2_adapter_rejects_non_list_targets_when_present():
    with pytest.raises(ValueError, match="targets"):
        RoomSupervisorService._parse_legacy_action_as_planner_action(
            {
                "action": "delegate",
                "reasoning": "test",
                "targets": "",
            }
        )


def test_v2_adapter_rejects_non_object_target():
    with pytest.raises(ValueError, match="target"):
        RoomSupervisorService._parse_legacy_action_as_planner_action(
            {
                "action": "delegate",
                "reasoning": "test",
                "targets": ["not an object"],
            }
        )


def test_v2_adapter_rejects_delegate_target_missing_agent_id():
    with pytest.raises(ValueError, match="agent_id"):
        RoomSupervisorService._parse_legacy_action_as_planner_action(
            {
                "action": "delegate",
                "reasoning": "test",
                "targets": [
                    {
                        "agent_name": "Agent One",
                        "task": "do work",
                    }
                ],
            }
        )


@pytest.mark.parametrize("task_value", [None, "", "  "])
def test_v2_adapter_rejects_delegate_target_missing_or_empty_task(task_value):
    target = {
        "agent_id": "agent-1",
        "agent_name": "Agent One",
    }
    if task_value is not None:
        target["task"] = task_value

    with pytest.raises(ValueError, match="task"):
        RoomSupervisorService._parse_legacy_action_as_planner_action(
            {
                "action": "delegate",
                "reasoning": "test",
                "targets": [target],
            }
        )
