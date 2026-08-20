from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import ValidationError, validate

from execution.orchestration.action_validator import (
    PlannerActionValidationError,
    PlannerActionValidator,
)
from execution.orchestration.context_builder import build_orchestration_planner_context
from execution.orchestration.planner import (
    PLANNER_ACTION_RESPONSE_SCHEMA,
    RoomSupervisorPlannerAdapter,
)
from execution.orchestration.room_supervisor_service import RoomSupervisorService
from models.orchestration import (
    ActiveDispatchRef,
    AgentOutputRecord,
    BlockerRecord,
    CandidateAgentSnapshot,
    CandidateScopeSnapshot,
    CompletionEvidence,
    DelegationOutcomeRecord,
    DispatchContentRef,
    DispatchExpectedOutput,
    DispatchIntent,
    DispatchRefKind,
    GoalFamilyDispositionRecord,
    GoalFamilyDispositionRequest,
    OpenFailureRecord,
    OrchestrationRunState,
    ParticipantSnapshot,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
    PlannerQuestion,
)


def _state_for_validation(
    *,
    open_questions: list[dict] | None = None,
) -> OrchestrationRunState:
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="message-1",
        goal="Coordinate this",
        candidate_agent_ids=["agent-1"],
        open_questions=list(open_questions or []),
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
    resource_fingerprints: dict[str, str] | None = None,
):
    return PlannerActionValidator.validate(
        action,
        candidate_agent_ids=candidate_agent_ids or ["agent-1", "agent-2"],
        steps_used=steps_used,
        step_budget=step_budget,
        has_agent_output=has_agent_output,
        resource_fingerprints=resource_fingerprints,
    )


def test_text_only_candidate_rejects_artifact_expected_output():
    state = _state_for_validation()
    state.candidate_scope = CandidateScopeSnapshot(
        snapshot_id="scope-text",
        source="mention",
        room_id="room-1",
        agent_ids=["agent-1"],
        agents=[
            CandidateAgentSnapshot(
                agent_id="agent-1",
                name="Text Agent",
                output_modes=["text"],
            )
        ],
    )
    target = _target()
    target.expected_outputs = [
        DispatchExpectedOutput(
            output_key="answer",
            kind="image/png",
        )
    ]

    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(
            _action(PlannerActionType.DELEGATE, targets=[target]),
            run_state=state,
        )

    assert exc_info.value.code == "unsupported_expected_output_mode"


def test_candidate_accepts_compatible_media_expected_output():
    state = _state_for_validation()
    state.candidate_scope = CandidateScopeSnapshot(
        snapshot_id="scope-image",
        source="mention",
        room_id="room-1",
        agent_ids=["agent-1"],
        agents=[
            CandidateAgentSnapshot(
                agent_id="agent-1",
                name="Image Agent",
                output_modes=["text", "image/png"],
            )
        ],
    )
    target = _target()
    target.expected_outputs = [
        DispatchExpectedOutput(output_key="image", kind="image/png")
    ]
    action = _action(PlannerActionType.DELEGATE, targets=[target])

    assert PlannerActionValidator.validate(action, run_state=state) is action


def test_text_expected_output_rejects_artifact_fields():
    state = _state_for_validation()
    target = _target()
    target.expected_outputs = [
        DispatchExpectedOutput(
            output_key="answer",
            kind="text",
            required_fields=["summary"],
        )
    ]

    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(
            _action(PlannerActionType.DELEGATE, targets=[target]),
            run_state=state,
        )

    assert exc_info.value.code == "invalid_text_output_contract"


def test_artifact_delivery_failure_forbids_regenerating_with_same_agent():
    state = _state_for_validation()
    state.open_failures = [
        OpenFailureRecord(
            failure_id="failure-artifact",
            fingerprint="artifact-family",
            source="a2a_adapter",
            agent_id="agent-1",
            agent_message_id="agent-msg-1",
            error_code="artifact_delivery_failed",
            error_message="Agent output could not be processed.",
            recoverable=False,
            status="open",
        )
    ]

    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(
            _action(PlannerActionType.DELEGATE, targets=[_target()]),
            run_state=state,
        )

    assert exc_info.value.code == "artifact_delivery_retry_forbidden"
    assert exc_info.value.recoverable is False


def test_artifact_delivery_failure_forbids_same_output_family_on_replacement_agent():
    state = _state_for_validation()
    state.candidate_agent_ids.append("agent-2")
    intent = _completed_quote_intent()
    intent.dispatch_intent_id = "failed-image-intent"
    intent.expected_outputs = [
        DispatchExpectedOutput(
            output_key="generated_image", kind="image/png", required=True
        )
    ]
    state.dispatch_intents = [intent]
    state.open_failures = [
        OpenFailureRecord(
            failure_id="failure-artifact",
            fingerprint="artifact-family",
            source="a2a_adapter",
            agent_id="agent-1",
            dispatch_intent_id=intent.dispatch_intent_id,
            error_code="artifact_delivery_failed",
            error_message="Agent output could not be processed.",
            recoverable=False,
            status="open",
        )
    ]
    target = _target("agent-2")
    target.expected_outputs = [
        DispatchExpectedOutput(
            output_key="replacement_image", kind="image/jpeg", required=True
        )
    ]

    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(
            _action(PlannerActionType.DELEGATE, targets=[target]),
            run_state=state,
        )

    assert exc_info.value.code == "artifact_delivery_retry_forbidden"


def test_repeated_generic_agent_failures_exhaust_operational_retry_budget():
    state = _state_for_validation()
    state.open_failures = [
        OpenFailureRecord(
            failure_id=f"failure-{index}",
            fingerprint=f"generic-{index}",
            source="a2a_adapter",
            agent_id="agent-1",
            agent_message_id=f"agent-msg-{index}",
            error_code="agent_execution_failed",
            error_message="Agent processing failed",
            recoverable=True,
            status="open",
        )
        for index in range(2)
    ]

    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(
            _action(PlannerActionType.DELEGATE, targets=[_target()]),
            run_state=state,
        )

    assert exc_info.value.code == "recovery_retry_exhausted"
    assert exc_info.value.recoverable is False


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
    action = _action(PlannerActionType.COMPLETE)

    result = _validate(action, has_agent_output=True)

    assert result is action


def test_budget_exhaustion_rejects_delegate_but_allows_complete():
    delegate = _action(PlannerActionType.DELEGATE, targets=[_target()])
    complete = _action(PlannerActionType.COMPLETE)

    with pytest.raises(
        PlannerActionValidationError,
        match="step budget",
    ) as delegate_error:
        _validate(delegate, steps_used=8, step_budget=8)
    assert delegate_error.value.code == "step_budget_exhausted"
    assert delegate_error.value.recoverable is False

    assert (
        _validate(
            complete,
            steps_used=8,
            step_budget=8,
            has_agent_output=True,
        )
        is complete
    )


def test_budget_exhaustion_allows_terminal_user_actions_and_fail():
    synthesize = _action(PlannerActionType.COMPLETE)
    ask_user = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="Need a user-only value",
        questions=[PlannerQuestion(prompt="What is the approved limit?")],
    )
    file_handoff = PlannerAction(
        action=PlannerActionType.REQUEST_FILE_HANDOFF,
        reasoning="Need the signed source file",
        file_prompt="Upload the signed source file in a new message.",
    )
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
    assert _validate(ask_user, steps_used=8, step_budget=8) is ask_user
    assert _validate(file_handoff, steps_used=8, step_budget=8) is file_handoff
    assert _validate(fail, steps_used=8, step_budget=8) is fail


def test_file_handoff_requires_prompt_and_forbids_questions():
    with pytest.raises(PlannerActionValidationError, match="file_prompt"):
        _validate(
            PlannerAction(
                action=PlannerActionType.REQUEST_FILE_HANDOFF,
                reasoning="missing prompt",
            )
        )
    with pytest.raises(PlannerActionValidationError, match="must not contain"):
        _validate(
            PlannerAction(
                action=PlannerActionType.REQUEST_FILE_HANDOFF,
                reasoning="mixed controls",
                file_prompt="Upload the file.",
                questions=[PlannerQuestion(prompt="Also answer this")],
            )
        )


def test_delegate_rejects_empty_targets():
    action = _action(PlannerActionType.DELEGATE)

    with pytest.raises(PlannerActionValidationError, match="target"):
        _validate(action)


def test_multi_target_delegate_without_parallel_group_is_host_normalized():
    action = _action(
        PlannerActionType.DELEGATE,
        targets=[_target(agent_id="agent-1"), _target(agent_id="agent-2")],
    )

    assert _validate(action, candidate_agent_ids=["agent-1", "agent-2"]) is action


def test_multi_target_delegate_with_shared_parallel_group_is_allowed():
    action = _action(
        PlannerActionType.DELEGATE,
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Summarize section A.",
                parallel_group="fanout-1",
            ),
            PlannedDelegateTarget(
                agent_id="agent-2",
                task="Summarize section B.",
                parallel_group="fanout-1",
            ),
        ],
    )

    assert _validate(action, candidate_agent_ids=["agent-1", "agent-2"]) is action


def test_multi_target_delegate_with_intra_action_dependency_is_rejected():
    action = _action(
        PlannerActionType.DELEGATE,
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Produce the upstream artifact.",
                parallel_group="fanout-1",
            ),
            PlannedDelegateTarget(
                agent_id="agent-2",
                task="Use the upstream artifact.",
                parallel_group="fanout-1",
                depends_on=["agent-1"],
            ),
        ],
    )

    with pytest.raises(PlannerActionValidationError) as exc_info:
        _validate(action, candidate_agent_ids=["agent-1", "agent-2"])

    assert exc_info.value.code == "parallel_dependency_unspecified"


def test_delegate_rejects_empty_target_task():
    action = _action(
        PlannerActionType.DELEGATE,
        targets=[_target(task="  ")],
    )

    with pytest.raises(PlannerActionValidationError, match="task"):
        _validate(action)


def test_delegate_rejects_resource_id_in_task_without_selecting_its_ref():
    action = _action(
        PlannerActionType.DELEGATE,
        targets=[
            _target(
                task=("Create a structured submission from file:application-pdf-1.")
            )
        ],
    )

    with pytest.raises(PlannerActionValidationError) as exc_info:
        _validate(
            action,
            resource_fingerprints={"file:application-pdf-1": "sha256:abc"},
        )

    assert exc_info.value.code == "delegate_resource_ref_omitted"


def test_delegate_allows_resource_id_in_task_when_selected_explicitly():
    action = _action(
        PlannerActionType.DELEGATE,
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task=("Create a structured submission from file:application-pdf-1."),
                attachment_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ATTACHMENT,
                        ref_id="file:application-pdf-1",
                    )
                ],
                required_resource_refs=["file:application-pdf-1"],
            )
        ],
    )

    assert (
        _validate(
            action,
            resource_fingerprints={"file:application-pdf-1": "sha256:abc"},
        )
        is action
    )


def test_ask_user_rejects_repeating_answered_supervisor_questions():
    state = _state_for_validation(
        open_questions=[
            {
                "request_id": "hitl-1",
                "source": "supervisor",
                "prompt": "Which account should we use?",
                "status": "resolved",
                "resolved": True,
                "answer": "Enterprise",
            }
        ],
    )
    action = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="Need the same input again.",
        questions=[
            PlannerQuestion(
                prompt="  Which account should we use? ",
                prompt_type="text",
            )
        ],
    )

    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=True,
        )

    assert exc_info.value.code == "duplicate_answered_question"
    assert exc_info.value.recoverable is True


def test_ask_user_allows_new_question_after_answered_supervisor_question():
    state = _state_for_validation(
        open_questions=[
            {
                "request_id": "hitl-1",
                "source": "supervisor",
                "prompt": "Which account should we use?",
                "status": "resolved",
                "resolved": True,
                "answer": "Enterprise",
            }
        ],
    )
    action = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="Need a new input.",
        questions=[
            PlannerQuestion(
                prompt="What deductible should we target?",
                prompt_type="text",
            )
        ],
    )

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=True,
        )
        is action
    )


def _question_action(reason: str, blocker_keys: list[str]) -> PlannerAction:
    return PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="request required user input",
        questions=[
            PlannerQuestion(
                prompt="Provide the missing required value",
                reason=reason,
                blocker_keys=list(blocker_keys),
            )
        ],
    )


def _completed_quote_intent() -> DispatchIntent:
    return DispatchIntent(
        step_id="i1",
        step_target_id="i1:target",
        dispatch_intent_id="i1",
        planned_agent_message_id="i1:message",
        agent_id="agent-1",
        task="produce quote",
        task_hash="task-hash",
        status="completed",
        expected_outputs=[
            DispatchExpectedOutput(
                output_key="quote",
                kind="artifact",
                required=True,
            )
        ],
    )


def _validated_quote_blocker(key: str) -> BlockerRecord:
    return BlockerRecord(
        key=key,
        description="The user must provide the required quote input.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
    )


def test_initial_clarification_uses_dispatch_history_not_steps_used():
    state = _guardrail_state()
    state.steps_used = 1
    action = _question_action("initial_clarification", [])

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=True,
            resource_fingerprints={},
        )
        is action
    )


def test_initial_clarification_rejects_duplicate_question_in_same_action():
    action = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="request required user input",
        questions=[
            PlannerQuestion(prompt="Which account should we use?"),
            PlannerQuestion(prompt="  Which account should we use?  "),
        ],
    )

    assert (
        _validation_code(action, _guardrail_state()) == "duplicate_question_in_action"
    )


def test_post_dispatch_question_requires_blocker_keys():
    state = _guardrail_state(intents=[_completed_quote_intent()])

    assert _validation_code(_question_action("blocker", []), state) == (
        "ask_user_blocker_keys_required"
    )


def test_post_dispatch_question_rejects_unvalidated_blocker():
    blocker = _validated_quote_blocker("quote-input").model_copy(
        update={
            "validated_user_only": False,
            "validation_status": "candidate",
        }
    )
    state = _guardrail_state(
        intents=[_completed_quote_intent()],
        blockers=[blocker],
    )

    assert _validation_code(_question_action("blocker", [blocker.key]), state) == (
        "ask_user_blocker_not_validated"
    )


def test_post_dispatch_question_accepts_validated_blocker():
    blocker = _validated_quote_blocker("quote-input")
    state = _guardrail_state(
        intents=[_completed_quote_intent()],
        blockers=[blocker],
    )
    action = _question_action("blocker", [blocker.key])

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=True,
            resource_fingerprints={},
        )
        is action
    )


def test_post_dispatch_question_accepts_validated_blocker_without_expected_outputs():
    """Prose delegates clear expected_outputs; HITL must still work."""
    blocker = _validated_quote_blocker("travel-dates")
    intent = _completed_quote_intent()
    intent.expected_outputs = []
    state = _guardrail_state(
        intents=[intent],
        blockers=[blocker],
    )
    action = _question_action("blocker", [blocker.key])

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=True,
            resource_fingerprints={},
        )
        is action
    )


@pytest.mark.parametrize("status", ["creating", "open", "resolved"])
def test_post_dispatch_question_rejects_pending_or_answered_duplicate(status: str):
    blocker = _validated_quote_blocker("quote-input")
    state = _guardrail_state(
        intents=[_completed_quote_intent()],
        blockers=[blocker],
        open_questions=[
            {
                "source": "supervisor",
                "prompt": "Provide the missing required value",
                "status": status,
                "resolved": status == "resolved",
            }
        ],
    )

    assert _validation_code(_question_action("blocker", [blocker.key]), state) == (
        "duplicate_answered_question"
        if status == "resolved"
        else "duplicate_pending_question"
    )


def test_post_dispatch_duplicate_question_is_allowed_when_guardrails_disabled():
    blocker = _validated_quote_blocker("quote-input")
    state = _guardrail_state(
        intents=[_completed_quote_intent()],
        blockers=[blocker],
        open_questions=[
            {
                "source": "supervisor",
                "prompt": "Provide the missing required value",
                "status": "open",
            }
        ],
    )
    action = _question_action("blocker", [blocker.key])

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=False,
            resource_fingerprints={},
        )
        is action
    )


def test_guardrail_flag_controls_enforcement_after_shadow_state_exists():
    target = PlannedDelegateTarget(agent_id="agent-1", task="Produce quote")
    fingerprints = PlannerActionValidator._target_goal_fingerprints(target, {})
    prior_intent = DispatchIntent(
        step_id="run-1:step-1",
        step_target_id="run-1:step-1:target-1",
        dispatch_intent_id="intent-1",
        planned_agent_message_id="agent-msg-1",
        agent_id="agent-1",
        task="Produce quote",
        task_hash="hash-1",
    )
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="Produce quote",
        candidate_agent_ids=["agent-1"],
        dispatch_intents=[prior_intent],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="agent-1",
                goal_family_fingerprint=fingerprints.goal_family_fingerprint,
                goal_revision_fingerprint=fingerprints.goal_revision_fingerprint,
                attempt_fingerprint="attempt-1",
                status="no_progress",
            )
        ],
    )
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Retry same work",
        targets=[target],
    )

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=False,
        )
        is action
    )
    with pytest.raises(PlannerActionValidationError):
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=True,
        )


def test_post_dispatch_empty_question_list_requires_blocker_keys():
    state = _guardrail_state(intents=[_completed_quote_intent()])
    action = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="request required user input",
        questions=[],
    )

    assert _validation_code(action, state) == "ask_user_blocker_keys_required"


def test_post_dispatch_whitespace_only_question_requires_normalized_prompt():
    blocker = _validated_quote_blocker("quote-input")
    state = _guardrail_state(
        intents=[_completed_quote_intent()],
        blockers=[blocker],
    )
    action = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="request required user input",
        questions=[
            PlannerQuestion(
                prompt=" \t ",
                reason="blocker",
                blocker_keys=[blocker.key],
            )
        ],
    )

    assert _validation_code(action, state) == "ask_user_blocker_keys_required"


def test_post_dispatch_rejects_duplicate_question_in_same_action():
    quote_blocker = _validated_quote_blocker("quote-input")
    terms_blocker = _validated_quote_blocker("quote-terms")
    state = _guardrail_state(
        intents=[_completed_quote_intent()],
        blockers=[quote_blocker, terms_blocker],
    )
    action = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="request required user input",
        questions=[
            PlannerQuestion(
                prompt="Provide the missing required value",
                reason="blocker",
                blocker_keys=[quote_blocker.key],
            ),
            PlannerQuestion(
                prompt="  Provide the missing required value  ",
                reason="blocker",
                blocker_keys=[terms_blocker.key],
            ),
        ],
    )

    assert _validation_code(action, state) == "duplicate_question_in_action"


def test_post_dispatch_rejects_duplicate_blocker_in_same_action():
    blocker = _validated_quote_blocker("quote-input")
    state = _guardrail_state(
        intents=[_completed_quote_intent()],
        blockers=[blocker],
    )
    action = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="request required user input",
        questions=[
            PlannerQuestion(
                prompt="Provide the missing required value",
                reason="blocker",
                blocker_keys=[blocker.key],
            ),
            PlannerQuestion(
                prompt="Which carrier should receive the quote request?",
                reason="blocker",
                blocker_keys=[blocker.key],
            ),
        ],
    )

    assert _validation_code(action, state) == "duplicate_question_in_action"


def test_post_dispatch_rejects_duplicate_blocker_within_one_question():
    blocker = _validated_quote_blocker("quote-input")
    state = _guardrail_state(
        intents=[_completed_quote_intent()],
        blockers=[blocker],
    )
    action = _question_action("blocker", [blocker.key, blocker.key])

    assert _validation_code(action, state) == "duplicate_question_in_action"


def test_post_dispatch_question_rejects_blocker_already_pending_under_new_prompt():
    blocker = _validated_quote_blocker("quote-input")
    state = _guardrail_state(
        intents=[_completed_quote_intent()],
        blockers=[blocker],
        open_questions=[
            {
                "source": "supervisor",
                "prompt": "Which carrier should receive the quote request?",
                "blocker_keys": [blocker.key],
                "status": "open",
            }
        ],
    )

    assert _validation_code(_question_action("blocker", [blocker.key]), state) == (
        "duplicate_pending_question"
    )


def test_post_dispatch_question_accepts_two_validated_blockers_in_one_request():
    quote_blocker = _validated_quote_blocker("quote-input")
    terms_blocker = _validated_quote_blocker("quote-terms")
    state = _guardrail_state(
        intents=[_completed_quote_intent()],
        blockers=[quote_blocker, terms_blocker],
    )
    action = _question_action("blocker", [quote_blocker.key, terms_blocker.key])

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=True,
            resource_fingerprints={},
        )
        is action
    )


@pytest.mark.asyncio
async def test_planner_adapter_accepts_artifact_refs_without_run_state():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Use the existing artifact.",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Use the quote artifact.",
                artifact_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ARTIFACT,
                        ref_id="artifact-1",
                    )
                ],
            )
        ],
    )
    context = build_orchestration_planner_context(
        run_state=_state_for_validation(),
        candidate_scope=["agent-1"],
        message_text="Use the artifact",
    )
    adapter = RoomSupervisorPlannerAdapter(raw_action_provider=lambda _context: action)

    result = await adapter.plan(context)

    assert result is action


@pytest.mark.asyncio
async def test_planner_adapter_preserves_raw_delegate_target_refs_and_policy():
    raw_action = {
        "action": "delegate",
        "reasoning": "Use selected references.",
        "targets": [
            {
                "agent_id": "agent-1",
                "agent_name": "Agent One",
                "task": "Underwrite with selected context.",
                "context_refs": [
                    {
                        "kind": "context",
                        "ref_id": "fact-1",
                        "source_agent_message_id": "agent-msg-1",
                        "mime_type": "text/plain",
                        "required": True,
                    }
                ],
                "artifact_refs": [
                    {
                        "kind": "artifact",
                        "ref_id": "artifact-1",
                        "mime_type": "application/json",
                        "required": False,
                    }
                ],
                "attachment_refs": [
                    {
                        "kind": "attachment",
                        "ref_id": "file-1",
                        "mime_type": "application/pdf",
                        "required": False,
                    }
                ],
                "expected_outputs": [
                    {
                        "kind": "quote_summary",
                        "required": True,
                        "description": "Summarize underwriting blockers.",
                    }
                ],
                "attachment_policy": "compatible_only",
            }
        ],
    }
    context = build_orchestration_planner_context(
        run_state=_state_for_validation(),
        candidate_scope=["agent-1"],
        message_text="Use selected refs",
    )
    adapter = RoomSupervisorPlannerAdapter(
        raw_action_provider=lambda _context: raw_action
    )

    result = await adapter.plan(context)

    target = result.targets[0]
    assert target.context_refs == [
        DispatchContentRef(
            kind=DispatchRefKind.CONTEXT,
            ref_id="fact-1",
            source_agent_message_id="agent-msg-1",
            mime_type="text/plain",
        )
    ]
    assert target.artifact_refs == [
        DispatchContentRef(
            kind=DispatchRefKind.ARTIFACT,
            ref_id="artifact-1",
            mime_type="application/json",
            required=False,
        )
    ]
    assert target.attachment_refs == [
        DispatchContentRef(
            kind=DispatchRefKind.ATTACHMENT,
            ref_id="file-1",
            mime_type="application/pdf",
            required=False,
        )
    ]
    assert target.expected_outputs == []
    assert target.attachment_policy == "compatible_only"


def test_planner_schema_does_not_expose_attachment_policy():
    target_schema = PLANNER_ACTION_RESPONSE_SCHEMA["properties"]["targets"]["items"]

    assert "attachment_policy" not in target_schema["properties"]
    assert "attachment_policy" not in target_schema["required"]


def test_planner_schema_accepts_business_level_delegate_without_control_fields():
    validate(
        {
            "action": "delegate",
            "reasoning": "Ask the selected specialist to review the submission.",
            "targets": [
                {
                    "agent_id": "agent-1",
                    "agent_name": "Agent One",
                    "task": "Review the submission and return missing underwriting facts.",
                    "parallel_group": None,
                    "depends_on": [],
                    "required_resource_refs": ["file:file-1"],
                    "context_refs": [],
                    "artifact_refs": [],
                    "attachment_refs": [],
                    "expected_outputs": [],
                }
            ],
            "questions": [],
            "synthesis_instruction": None,
            "failure_reason": None,
            "completion_evidence": None,
        },
        PLANNER_ACTION_RESPONSE_SCHEMA,
    )


def test_planner_schema_accepts_business_level_question_with_empty_blocker_keys():
    validate(
        {
            "action": "ask_user",
            "reasoning": "The next step needs information only the user can provide.",
            "targets": [],
            "questions": [
                {
                    "prompt": "What is the applicant's annual revenue?",
                    "prompt_type": "text",
                    "choices": None,
                    "reason": "initial_clarification",
                    "blocker_keys": [],
                }
            ],
            "synthesis_instruction": None,
            "failure_reason": None,
            "completion_evidence": None,
        },
        PLANNER_ACTION_RESPONSE_SCHEMA,
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "event_id",
        "goal_family_fingerprint",
        "through_goal_revision_fingerprint",
        "reason",
    ],
)
def test_completion_disposition_request_rejects_blank_required_fields(field_name):
    payload = {
        "event_id": "dispose-1",
        "goal_family_fingerprint": "family-1",
        "through_goal_revision_fingerprint": "revision-1",
        "status": "abandoned",
        "reason": "The user withdrew the request.",
    }
    payload[field_name] = " "

    with pytest.raises(ValueError, match="nonempty"):
        GoalFamilyDispositionRequest(**payload)


def test_planner_schema_rejects_blank_completion_disposition_reason():
    disposition_schema = PLANNER_ACTION_RESPONSE_SCHEMA["properties"][
        "completion_evidence"
    ]["anyOf"][0]["properties"]["requested_goal_family_dispositions"]["items"]

    with pytest.raises(ValidationError, match="reason"):
        validate(
            {
                "event_id": "dispose-1",
                "goal_family_fingerprint": "family-1",
                "through_goal_revision_fingerprint": "revision-1",
                "status": "abandoned",
                "reason": " ",
                "replacement_goal_family_fingerprint": None,
            },
            disposition_schema,
        )


def test_planner_response_schema_is_openai_strict_compatible():
    def assert_object_required_matches_properties(schema, path="$"):
        if isinstance(schema, dict):
            properties = schema.get("properties")
            if properties:
                assert set(schema.get("required", [])) == set(properties), path
            for key, value in schema.items():
                assert_object_required_matches_properties(value, f"{path}.{key}")
        elif isinstance(schema, list):
            for index, value in enumerate(schema):
                assert_object_required_matches_properties(value, f"{path}[{index}]")

    assert_object_required_matches_properties(PLANNER_ACTION_RESPONSE_SCHEMA)


def test_completion_disposition_schema_accepts_nullable_replacement_fingerprint():
    completion_schema = PLANNER_ACTION_RESPONSE_SCHEMA["properties"][
        "completion_evidence"
    ]["anyOf"][0]
    disposition_schema = PLANNER_ACTION_RESPONSE_SCHEMA["properties"][
        "completion_evidence"
    ]["anyOf"][0]["properties"]["requested_goal_family_dispositions"]["items"]

    assert set(completion_schema["required"]) == set(completion_schema["properties"])
    assert set(disposition_schema["required"]) == set(disposition_schema["properties"])
    validate(
        {
            "event_id": "dispose-1",
            "goal_family_fingerprint": "family-1",
            "through_goal_revision_fingerprint": "revision-1",
            "status": "abandoned",
            "reason": "The user withdrew the request.",
            "replacement_goal_family_fingerprint": None,
        },
        disposition_schema,
    )


def test_planner_schema_and_parser_accept_completion_output_evidence_fields():
    payload = {
        "action": "complete",
        "reasoning": "The quote is ready.",
        "targets": [],
        "questions": [],
        "synthesis_instruction": None,
        "failure_reason": None,
        "completion_evidence": {
            "satisfied_criteria": ["quote_collected"],
            "referenced_fact_ids": ["fact-1"],
            "referenced_artifact_keys": ["artifact-1"],
            "unresolved_questions": [],
            "final_answer_intent": "answer_user",
            "confidence": 0.8,
            "satisfied_output_keys": ["quote"],
            "waived_outputs": [
                {
                    "output_key": "non_required_addendum",
                    "reason": "The user did not request the addendum.",
                    "blocker_keys": [],
                }
            ],
            "abandoned_goal_disposition_event_ids": [],
            "requested_goal_family_dispositions": [],
        },
    }

    validate(payload, PLANNER_ACTION_RESPONSE_SCHEMA)

    action = RoomSupervisorPlannerAdapter()._parse_action(payload)

    assert action.completion_evidence.satisfied_output_keys == ["quote"]
    assert action.completion_evidence.waived_outputs[0].reason == (
        "The user did not request the addendum."
    )


def test_completion_validator_rejects_disposition_without_reason():
    action = _complete_action()
    action.completion_evidence.requested_goal_family_dispositions.append(
        GoalFamilyDispositionRequest.model_construct(
            event_id="dispose-1",
            goal_family_fingerprint="family-1",
            through_goal_revision_fingerprint="revision-1",
            status="abandoned",
            reason=" ",
        )
    )

    with pytest.raises(PlannerActionValidationError) as exc:
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(),
        )

    assert exc.value.code == "completion_disposition_request_invalid"


def test_completion_validator_rejects_unknown_disposition_revision_metadata():
    action = _complete_action(
        abandoned_goal_disposition_event_ids=["dispose-1"],
        requested_goal_family_dispositions=[
            {
                "event_id": "dispose-1",
                "goal_family_fingerprint": "family-1",
                "through_goal_revision_fingerprint": "unknown-revision",
                "status": "abandoned",
                "reason": "The requested revision is no longer needed.",
            }
        ],
    )
    state = _complete_run_state(
        delegation_outcomes=[
            _completion_outcome("outcome-1", "intent-1", remaining=["quote"])
        ]
    )

    with pytest.raises(PlannerActionValidationError) as exc:
        PlannerActionValidator.validate(action, run_state=state)

    assert exc.value.code == "completion_disposition_unreferenced"


def test_provider_planner_parser_defaults_absent_outcome_policy_fields():
    action = RoomSupervisorService._parse_provider_action_as_planner_action(
        {
            "action": "delegate",
            "reasoning": "Use the selected specialist.",
            "targets": [
                {
                    "agent_id": "agent-1",
                    "task": "Provide a summary.",
                    "expected_outputs": [{"kind": "summary"}],
                }
            ],
            "questions": [{"prompt": "What should we prioritize?"}],
        }
    )

    target = action.targets[0]
    assert target.repair_of_intent_id is None
    assert target.expected_outputs[0] == DispatchExpectedOutput(kind="summary")
    assert action.questions[0] == PlannerQuestion(prompt="What should we prioritize?")


@pytest.mark.asyncio
async def test_planner_adapter_defaults_missing_attachment_policy_to_explicit_refs_only():
    raw_action = {
        "action": "delegate",
        "reasoning": "Use selected projection.",
        "targets": [
            {
                "agent_id": "agent-1",
                "agent_name": "Agent One",
                "task": "Read the selected text projection.",
                "context_refs": [
                    {
                        "kind": "context",
                        "ref_id": "ctx:file-file-1:text",
                        "source_agent_message_id": None,
                        "mime_type": "text/plain",
                        "required": True,
                    }
                ],
                "artifact_refs": [],
                "attachment_refs": [],
                "expected_outputs": [
                    {
                        "kind": "summary",
                        "required": True,
                        "description": "Summarize the projection.",
                    }
                ],
            }
        ],
        "questions": [],
        "synthesis_instruction": None,
        "failure_reason": None,
        "completion_evidence": None,
    }
    context = build_orchestration_planner_context(
        run_state=_state_for_validation(),
        candidate_scope=["agent-1"],
        message_text="Use selected refs",
    )
    adapter = RoomSupervisorPlannerAdapter(
        raw_action_provider=lambda _context: raw_action
    )

    result = await adapter.plan(context)

    assert result.targets[0].attachment_policy == "explicit_refs_only"


@pytest.mark.asyncio
async def test_planner_adapter_requests_strict_planner_action_schema():
    class FakeSupervisorService:
        def __init__(self):
            self.schema = None
            self.system_prompt = None

        async def call_planner_json(self, *, system_prompt, user_prompt, schema=None):
            self.schema = schema
            self.system_prompt = system_prompt
            return {
                "action": "delegate",
                "reasoning": "Use selected agent.",
                "targets": [
                    {
                        "agent_id": "agent-1",
                        "task": "Do the requested work.",
                        "expected_outputs": [
                            {
                                "kind": "summary",
                                "required": True,
                                "description": "Summarize the result.",
                            }
                        ],
                    }
                ],
            }

        @staticmethod
        def parse_planner_action(response_json):
            return RoomSupervisorService.parse_planner_action(response_json)

    supervisor_service = FakeSupervisorService()
    context = build_orchestration_planner_context(
        run_state=_state_for_validation(),
        candidate_scope=["agent-1"],
        message_text="Use selected refs",
    )
    adapter = RoomSupervisorPlannerAdapter(supervisor_service=supervisor_service)

    await adapter.plan(context)

    assert supervisor_service.schema is not None
    schema = supervisor_service.schema
    target_schema = schema["properties"]["targets"]["items"]
    assert target_schema["properties"]["agent_id"]["enum"] == ["agent-1"]
    assert "parallel_group" not in target_schema["properties"]
    assert "depends_on" not in target_schema["properties"]
    assert "decision_summary" in schema["required"]
    assert "reasoning" not in schema["properties"]
    assert "synthesize" not in schema["properties"]["action"]["enum"]
    assert "Execution generates" in supervisor_service.system_prompt


def test_delegate_rejects_unknown_required_artifact_ref():
    state = _state_for_validation()
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Use missing artifact",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Use the missing artifact.",
                artifact_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ARTIFACT,
                        ref_id="missing",
                    )
                ],
            )
        ],
    )

    with pytest.raises(PlannerActionValidationError, match="unknown artifact"):
        PlannerActionValidator.validate(
            action,
            candidate_agent_ids=["agent-1"],
            run_state=state,
        )


@pytest.mark.parametrize(
    "action_type",
    [PlannerActionType.COMPLETE, PlannerActionType.COMPLETE],
)
def test_complete_and_synthesize_before_agent_output_are_rejected(action_type):
    action = _action(action_type)

    with pytest.raises(PlannerActionValidationError, match="agent output"):
        _validate(action, has_agent_output=False)


def test_platform_answer_before_agent_output_is_allowed():
    action = PlannerAction(
        action=PlannerActionType.PLATFORM_ANSWER,
        reasoning="No suitable agent in the selected scope.",
        synthesis_instruction=(
            "Answer directly and disclose that the connected agents do not cover "
            "this request."
        ),
    )

    result = _validate(action, has_agent_output=False)

    assert result is action


def test_platform_answer_is_allowed_after_step_budget_is_exhausted():
    action = PlannerAction(
        action=PlannerActionType.PLATFORM_ANSWER,
        reasoning="Agent execution failed and no alternate remains.",
        synthesis_instruction="Answer directly and disclose the execution failure.",
    )

    result = _validate(action, steps_used=8, step_budget=8)

    assert result is action


def test_platform_answer_is_part_of_planner_response_schema():
    payload = {
        "action": "platform_answer",
        "reasoning": "No suitable agent in scope.",
        "targets": [],
        "questions": [],
        "synthesis_instruction": "Answer directly with a capability limitation.",
        "failure_reason": None,
        "completion_evidence": None,
    }

    validate(payload, PLANNER_ACTION_RESPONSE_SCHEMA)
    parsed = RoomSupervisorService.parse_planner_action(payload)

    assert parsed.action == PlannerActionType.PLATFORM_ANSWER


def test_planner_parser_canonicalizes_contradictory_text_output_metadata():
    action = RoomSupervisorService.parse_planner_action(
        {
            "action": "delegate",
            "decision_summary": "Check the weather.",
            "targets": [
                {
                    "agent_id": "agent-1",
                    "task": "Return the forecast as text.",
                    "expected_outputs": [
                        {
                            "output_key": "forecast",
                            "kind": "text",
                            "required": True,
                            "description": "Written forecast.",
                            "artifact_name": "weather_report",
                            "required_fields": ["temperature", "conditions"],
                            "allow_partial": False,
                        }
                    ],
                }
            ],
            "questions": [],
            "synthesis_instruction": None,
            "failure_reason": None,
            "completion_evidence": None,
        }
    )

    output = action.targets[0].expected_outputs[0]
    assert output.kind == "text"
    assert output.artifact_name is None
    assert output.required_fields == []
    assert output.output_key == "forecast"


def test_planner_schema_requires_listed_outputs_and_disallows_invented_names():
    output_schema = PLANNER_ACTION_RESPONSE_SCHEMA["properties"]["targets"]["items"][
        "properties"
    ]["expected_outputs"]["items"]["properties"]

    assert output_schema["required"]["const"] is True
    assert output_schema["artifact_name"] == {"type": "null"}


def test_planner_prompt_is_compact_and_execution_owned():
    from execution.orchestration.planner_prompt import PLANNER_SYSTEM_PROMPT

    prompt = PLANNER_SYSTEM_PROMPT.lower()
    assert "there is no synthesize action" in prompt
    assert "execution generates all ids and parallel groups" in prompt
    assert "never repeat an identical request without new evidence" in prompt
    assert "use text expected outputs for text-only agents" in prompt
    assert "never invent a caption, filename" in prompt
    assert "never relabel an ordinary written answer" in prompt
    assert "decision_summary under 500 characters" in prompt
    assert "private chain-of-thought" in prompt


def test_parse_planner_action_preserves_multi_target_parallel_group_fields():
    payload = {
        "action": "delegate",
        "reasoning": "Fan out independent travel and weather work.",
        "targets": [
            {
                "agent_id": "agent-travel",
                "agent_name": "Travel Planner",
                "task": "Create a 7-day Hawaii plan for 4 people.",
                "parallel_group": "hawaii-fanout",
                "depends_on": [],
                "required_resource_refs": ["res-1"],
                "context_refs": [],
                "artifact_refs": [],
                "attachment_refs": [],
                "expected_outputs": [],
            },
            {
                "agent_id": "agent-weather",
                "agent_name": "Weather Agent",
                "task": "Summarize Hawaii weather for the past month.",
                "parallel_group": "hawaii-fanout",
                "depends_on": [],
                "required_resource_refs": [],
                "context_refs": [],
                "artifact_refs": [],
                "attachment_refs": [],
                "expected_outputs": [],
            },
        ],
        "questions": [],
        "synthesis_instruction": None,
        "failure_reason": None,
        "completion_evidence": None,
    }

    parsed = RoomSupervisorService.parse_planner_action(payload)

    assert [target.parallel_group for target in parsed.targets] == [
        "hawaii-fanout",
        "hawaii-fanout",
    ]
    assert [target.depends_on for target in parsed.targets] == [[], []]
    assert parsed.targets[0].required_resource_refs == ["res-1"]
    assert parsed.targets[1].required_resource_refs == []


@pytest.mark.asyncio
async def test_planner_adapter_normalizes_blank_independent_parallel_group():
    adapter = RoomSupervisorPlannerAdapter(
        raw_action_provider=lambda _context: {
            "action": "delegate",
            "reasoning": "Independent travel and weather tasks.",
            "targets": [
                {
                    "agent_id": "agent-1",
                    "agent_name": "Travel",
                    "task": "Plan Hawaii trip.",
                    "parallel_group": None,
                    "depends_on": [],
                    "required_resource_refs": [],
                    "context_refs": [],
                    "artifact_refs": [],
                    "attachment_refs": [],
                    "expected_outputs": [],
                },
                {
                    "agent_id": "agent-2",
                    "agent_name": "Weather",
                    "task": "Check Hawaii weather.",
                    "parallel_group": None,
                    "depends_on": [],
                    "required_resource_refs": [],
                    "context_refs": [],
                    "artifact_refs": [],
                    "attachment_refs": [],
                    "expected_outputs": [],
                },
            ],
            "questions": [],
            "synthesis_instruction": None,
            "failure_reason": None,
            "completion_evidence": None,
        }
    )
    context = build_orchestration_planner_context(
        run_state=OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Plan Hawaii and check weather",
            candidate_agent_ids=["agent-1", "agent-2"],
        ),
        candidate_scope=["agent-1", "agent-2"],
        message_text="Plan Hawaii and check weather",
    )

    action = await adapter.plan(context)

    assert action.action == PlannerActionType.DELEGATE
    assert len(action.targets) == 2
    assert action.targets[0].parallel_group == action.targets[1].parallel_group
    assert action.targets[0].parallel_group
    assert action.targets[0].parallel_group.startswith("fanout-")


@pytest.mark.asyncio
async def test_planner_adapter_keeps_enforceable_text_expected_output():
    adapter = RoomSupervisorPlannerAdapter(
        raw_action_provider=lambda _context: {
            "action": "delegate",
            "reasoning": "Ask weather agent.",
            "targets": [
                {
                    "agent_id": "agent-1",
                    "agent_name": "Weather",
                    "task": "Check Hawaii weather for the past month.",
                    "parallel_group": None,
                    "depends_on": [],
                    "required_resource_refs": [],
                    "context_refs": [],
                    "artifact_refs": [],
                    "attachment_refs": [],
                    "expected_outputs": [
                        {
                            "output_key": "weather_past_month",
                            "kind": "text",
                            "required": True,
                            "description": "Past month weather",
                            "artifact_name": None,
                            "required_fields": [],
                            "allow_partial": False,
                        }
                    ],
                }
            ],
            "questions": [],
            "synthesis_instruction": None,
            "failure_reason": None,
            "completion_evidence": None,
        }
    )
    context = build_orchestration_planner_context(
        run_state=OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Check Hawaii weather",
            candidate_agent_ids=["agent-1"],
        ),
        candidate_scope=["agent-1"],
        message_text="Check Hawaii weather",
    )

    action = await adapter.plan(context)

    assert action.action == PlannerActionType.DELEGATE
    assert [output.kind for output in action.targets[0].expected_outputs] == ["text"]


@pytest.mark.asyncio
async def test_planner_adapter_filters_prose_json_fields_before_validation():
    adapter = RoomSupervisorPlannerAdapter(
        raw_action_provider=lambda _context: {
            "action": "delegate",
            "reasoning": "Prepare an underwriting submission.",
            "targets": [
                {
                    "agent_id": "agent-1",
                    "agent_name": "Broker",
                    "task": "Prepare the submission.",
                    "parallel_group": None,
                    "depends_on": [],
                    "required_resource_refs": [],
                    "context_refs": [],
                    "artifact_refs": [],
                    "attachment_refs": [],
                    "expected_outputs": [
                        {
                            "output_key": "submission",
                            "kind": "application/json",
                            "required": True,
                            "description": "Underwriting submission.",
                            "artifact_name": None,
                            "required_fields": [
                                "Client",
                                "Annual Revenue",
                                "premium",
                                "pricing.currency",
                                "_id",
                                "2fa_enabled",
                                "PascalCase",
                            ],
                            "allow_partial": False,
                        }
                    ],
                }
            ],
            "questions": [],
            "synthesis_instruction": None,
            "failure_reason": None,
            "completion_evidence": None,
        }
    )
    context = build_orchestration_planner_context(
        run_state=OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Prepare an underwriting submission",
            candidate_agent_ids=["agent-1"],
        ),
        candidate_scope=["agent-1"],
        message_text="Prepare an underwriting submission",
    )

    action = await adapter.plan(context)

    output = action.targets[0].expected_outputs[0]
    assert output.kind == "application/json"
    assert output.required is True
    assert output.required_fields == [
        "premium",
        "pricing.currency",
        "_id",
        "2fa_enabled",
        "PascalCase",
    ]


def test_planner_prompt_requires_domain_supported_agent_suitability():
    import inspect

    source = inspect.getsource(RoomSupervisorPlannerAdapter._call_supervisor_service)

    assert "Agent Card's" in source
    assert "accepting text" in source
    assert "unrelated " in source
    assert '"domain.' in source


@pytest.mark.asyncio
async def test_planner_schema_limits_agent_ids_to_candidate_scope():
    supervisor_service = SimpleNamespace(
        call_planner_json=AsyncMock(
            return_value={
                "action": "platform_answer",
                "decision_summary": "Answer directly.",
                "targets": [],
                "questions": [],
                "synthesis_instruction": "Reply naturally.",
            }
        ),
        parse_planner_action=RoomSupervisorService.parse_planner_action,
    )
    context = build_orchestration_planner_context(
        run_state=_state_for_validation(),
        candidate_scope=["agent-1"],
        message_text="hi",
    )

    await RoomSupervisorPlannerAdapter(supervisor_service=supervisor_service).plan(
        context
    )

    schema = supervisor_service.call_planner_json.await_args.kwargs["schema"]
    assert schema["properties"]["targets"]["items"]["properties"]["agent_id"][
        "enum"
    ] == ["agent-1"]


def test_complete_allowed_after_agent_output_before_budget_exhaustion():
    action = _action(PlannerActionType.COMPLETE)

    result = _validate(action, has_agent_output=True)

    assert result is action


def _scope():
    return CandidateScopeSnapshot(
        snapshot_id="scope-1",
        source="explicit_selection",
        room_id="room-1",
        agent_ids=["agent-1"],
        agents=[CandidateAgentSnapshot(agent_id="agent-1", name="Agent One")],
    )


def _complete_run_state(**overrides):
    values = {
        "run_id": "run-1",
        "room_id": "room-1",
        "user_message_id": "msg-1",
        "goal": "Collect evidence",
        "candidate_agent_ids": ["agent-1"],
        "candidate_scope": _scope(),
        "agent_outputs": [
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status="completed",
                text="answer",
            )
        ],
        "facts": [{"fact_id": "fact-1", "text": "quote is available"}],
        "artifacts": [{"artifact_key": "artifact-1", "name": "quote"}],
    }
    values.update(overrides)
    return OrchestrationRunState(**values)


def _complete_action(**evidence_overrides):
    evidence = {
        "satisfied_criteria": ["quote_collected"],
        "referenced_fact_ids": ["fact-1"],
        "referenced_artifact_keys": ["artifact-1"],
        "unresolved_questions": [],
        "final_answer_intent": "answer_user",
        "confidence": 0.8,
    }
    evidence.update(evidence_overrides)
    return PlannerAction(
        action=PlannerActionType.COMPLETE,
        reasoning="goal satisfied",
        completion_evidence=CompletionEvidence(**evidence),
    )


def _synthesize_action():
    return PlannerAction(
        action=PlannerActionType.COMPLETE,
        reasoning="Summarize the completed work.",
        synthesis_instruction="Write the final answer.",
    )


def _failure(status: str, *, recoverable: bool = True) -> OpenFailureRecord:
    return OpenFailureRecord(
        failure_id=f"failure-{status}-{recoverable}",
        fingerprint=f"fp-{status}-{recoverable}",
        source="a2a_adapter",
        agent_id="agent-1",
        agent_message_id="agent-msg-2",
        error_code="timeout",
        error_message="Timed out",
        recoverable=recoverable,
        status=status,
        recovery_hints=["retry_same_agent_with_smaller_context"],
    )


def _completion_outcome(
    outcome_id: str,
    intent_id: str,
    *,
    family: str = "family-1",
    revision: str = "revision-1",
    remaining: list[str] | None = None,
) -> DelegationOutcomeRecord:
    return DelegationOutcomeRecord(
        outcome_id=outcome_id,
        dispatch_intent_id=intent_id,
        agent_id="agent-1",
        goal_family_fingerprint=family,
        goal_revision_fingerprint=revision,
        attempt_fingerprint=f"attempt-{outcome_id}",
        status="partial" if remaining else "fulfilled",
        remaining_required_obligations=list(remaining or []),
    )


def _completion_case(case: str):
    action = _complete_action()
    cases = {
        "active_missing_obligation": (
            action,
            _complete_run_state(
                delegation_outcomes=[
                    _completion_outcome("outcome-1", "intent-1", remaining=["quote"])
                ]
            ),
        ),
        "unreferenced_disposition": (
            action,
            _complete_run_state(
                delegation_outcomes=[
                    _completion_outcome("outcome-1", "intent-1", remaining=["quote"])
                ],
                goal_family_dispositions=[
                    GoalFamilyDispositionRecord(
                        event_id="dispose-1",
                        goal_family_fingerprint="family-1",
                        through_goal_revision_fingerprint="revision-1",
                        status="abandoned",
                        reason="No longer needed.",
                    )
                ],
            ),
        ),
        "open_runtime_failure": (
            action,
            _complete_run_state(
                open_failures=[
                    OpenFailureRecord(
                        failure_id="runtime-failure-1",
                        fingerprint="runtime-fingerprint-1",
                        source="runtime",
                        error_code="transport_error",
                        error_message="Connection reset.",
                        recoverable=True,
                        status="open",
                    )
                ]
            ),
        ),
        "pending_hitl": (
            action,
            _complete_run_state(pending_hitl_request_ids=["hitl-1"]),
        ),
        "validated_open_blocker": (
            action,
            _complete_run_state(
                blockers=[
                    BlockerRecord(
                        key="missing-quote",
                        description="Quote cannot be completed without user input.",
                        blocked_output_keys=["quote"],
                        source="agent",
                        claimed_user_only=True,
                        validated_user_only=True,
                        validation_status="validated",
                    )
                ]
            ),
        ),
    }
    return cases[case]


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("open_runtime_failure", "completion_open_failure"),
        ("pending_hitl", "completion_pending_hitl"),
        ("validated_open_blocker", "completion_open_blocker"),
    ],
)
def test_completion_scope_rejections(case, expected_code):
    action, state = _completion_case(case)

    assert _validation_code(action, state) == expected_code


def test_open_runtime_failures_block_complete_without_feature_guardrails():
    _, state = _completion_case("open_runtime_failure")

    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(
            _complete_action(),
            run_state=state,
            guardrails_enabled=False,
            resource_fingerprints={},
        )

    assert exc_info.value.code == "completion_gate_rejected"


def test_completion_accepts_satisfied_latest_active_revision_obligations():
    state = _complete_run_state(
        delegation_outcomes=[
            _completion_outcome(
                "outcome-1", "intent-1", revision="revision-1", remaining=["quote"]
            ),
            _completion_outcome("outcome-2", "intent-2", revision="revision-2"),
        ]
    )

    assert PlannerActionValidator.validate(
        _complete_action(satisfied_output_keys=["quote"]),
        run_state=state,
        guardrails_enabled=True,
    )


def test_completion_accepts_referenced_abandoned_family_without_output_waivers():
    state = _complete_run_state(
        delegation_outcomes=[
            _completion_outcome("outcome-1", "intent-1", remaining=["quote"])
        ],
        goal_family_dispositions=[
            GoalFamilyDispositionRecord(
                event_id="dispose-1",
                goal_family_fingerprint="family-1",
                through_goal_revision_fingerprint="revision-1",
                status="abandoned",
                reason="The user withdrew the request.",
            )
        ],
    )

    assert PlannerActionValidator.validate(
        _complete_action(abandoned_goal_disposition_event_ids=["dispose-1"]),
        run_state=state,
        guardrails_enabled=True,
    )


@pytest.mark.parametrize(
    "status",
    [
        "success",
        "completed",
        "failed",
        "canceled",
        "rejected",
        "expired",
        "abandoned",
    ],
)
def test_completion_accepts_terminal_active_dispatch(status):
    state = _complete_run_state(
        active_dispatches=[
            ActiveDispatchRef(
                agent_message_id="agent-msg-2",
                agent_id="agent-1",
                status=status,
            )
        ]
    )

    assert PlannerActionValidator.validate(_complete_action(), run_state=state)


def test_completion_accepts_abandoned_failure():
    state = _complete_run_state(open_failures=[_failure("abandoned")])

    assert PlannerActionValidator.validate(_complete_action(), run_state=state)


def test_complete_does_not_require_structured_evidence():
    action = PlannerAction(action=PlannerActionType.COMPLETE, reasoning="done")

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(),
        )
        is action
    )


def test_complete_ignores_unknown_fact_reference_metadata():
    action = _complete_action(referenced_fact_ids=["missing-fact"])

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(),
        )
        is action
    )


def test_complete_rejects_pending_hitl_and_active_dispatches():
    action = _complete_action()

    with pytest.raises(PlannerActionValidationError, match="pending HITL"):
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(pending_hitl_request_ids=["hitl-1"]),
        )

    with pytest.raises(
        PlannerActionValidationError, match="active dispatch"
    ) as exc_info:
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(
                active_dispatches=[
                    ActiveDispatchRef(
                        agent_message_id="agent-msg-2",
                        agent_id="agent-1",
                        status="running",
                    )
                ]
            ),
        )
    assert exc_info.value.code == "completion_required_output_missing"


@pytest.mark.parametrize(
    ("state_overrides", "expected_code"),
    [
        ({"pending_hitl_request_ids": ["hitl-1"]}, "completion_pending_hitl"),
        (
            {
                "active_dispatches": [
                    ActiveDispatchRef(
                        agent_message_id="agent-msg-2",
                        agent_id="agent-1",
                        status="running",
                    )
                ]
            },
            "completion_required_output_missing",
        ),
    ],
)
def test_complete_state_preconditions_take_priority_without_output(
    state_overrides, expected_code
):
    state = _complete_run_state(agent_outputs=[], facts=[], **state_overrides)

    assert _validation_code(_complete_action(), state) == expected_code


def test_complete_rejected_when_recoverable_failure_is_open():
    state = _complete_run_state(
        open_failures=[
            OpenFailureRecord(
                failure_id="failure-1",
                fingerprint="fp",
                source="a2a_adapter",
                agent_id="agent-1",
                agent_message_id="agent-msg-1",
                error_code="timeout",
                error_message="Timed out",
                recoverable=True,
                status="open",
                recovery_hints=["retry_same_agent_with_smaller_context"],
            )
        ]
    )

    with pytest.raises(PlannerActionValidationError, match="open runtime failure"):
        PlannerActionValidator.validate(
            _complete_action(),
            run_state=state,
            guardrails_enabled=True,
        )


def test_complete_rejects_open_planner_validation_failure():
    state = _complete_run_state(
        open_failures=[
            OpenFailureRecord(
                failure_id="planner-failure-1",
                fingerprint="planner-validator-fp",
                source="planner_validator",
                error_code="planner_output_invalid",
                error_message="planner output was invalid",
                recoverable=True,
                status="open",
                recovery_hints=["replan_with_valid_schema"],
            )
        ]
    )
    action = _complete_action()

    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(action, run_state=state)
    assert exc_info.value.code == "completion_gate_rejected"


def test_complete_allows_abandoned_recoverable_failure():
    state = _complete_run_state(open_failures=[_failure("abandoned")])

    action = _complete_action()

    assert PlannerActionValidator.validate(action, run_state=state) is action


def test_synthesize_rejected_when_runtime_failure_is_open():
    state = _complete_run_state(open_failures=[_failure("open")])

    with pytest.raises(PlannerActionValidationError, match="open runtime failure"):
        PlannerActionValidator.validate(
            _synthesize_action(),
            run_state=state,
            guardrails_enabled=True,
        )


@pytest.mark.parametrize(
    "failure",
    [
        _failure("resolved"),
        _failure("abandoned"),
        _failure("open", recoverable=False),
        _failure("abandoned", recoverable=False),
    ],
)
def test_synthesize_allows_resolved_or_nonrecoverable_failures(failure):
    action = _synthesize_action()

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(open_failures=[failure]),
        )
        is action
    )


@pytest.mark.parametrize(
    "error_code",
    [
        "attachment_ref_not_found",
        "context_ref_not_found",
        "artifact_ref_not_found",
        "dispatch_payload_ref_unresolved",
    ],
)
@pytest.mark.parametrize("action_factory", [_synthesize_action, _complete_action])
def test_terminal_action_allows_open_reference_failure(
    error_code,
    action_factory,
):
    failure = _failure("open")
    failure.error_code = error_code
    action = action_factory()

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(open_failures=[failure]),
        )
        is action
    )


def test_complete_accepts_rejected_active_dispatch_reference():
    action = _complete_action()

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(
                active_dispatches=[
                    ActiveDispatchRef(
                        agent_message_id="agent-msg-2",
                        agent_id="agent-1",
                        status="rejected",
                    )
                ]
            ),
        )
        is action
    )


@pytest.mark.parametrize(
    "open_question",
    [
        {"question_id": "question-1", "text": "Need more detail"},
        {
            "question_id": "question-1",
            "text": "Need more detail",
            "resolved": False,
            "blocking": False,
        },
    ],
)
def test_complete_rejects_every_open_question(open_question):
    action = _complete_action()

    with pytest.raises(PlannerActionValidationError, match="unresolved questions"):
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(open_questions=[open_question]),
        )


def test_complete_accepts_resolved_question_history():
    action = _complete_action()

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(
                open_questions=[
                    {
                        "question_id": "question-1",
                        "text": "Need more detail",
                        "status": "resolved",
                        "resolved": True,
                    }
                ]
            ),
        )
        is action
    )


def test_complete_ignores_blank_satisfied_criteria_metadata():
    action = _complete_action(satisfied_criteria=["  "])

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(),
        )
        is action
    )


def test_complete_accepts_valid_evidence():
    action = _complete_action()

    assert (
        PlannerActionValidator.validate(action, run_state=_complete_run_state())
        is action
    )


def test_complete_accepts_valid_evidence_with_facts_only():
    action = _complete_action()

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(agent_outputs=[]),
        )
        is action
    )


@pytest.mark.asyncio
async def test_planner_adapter_accepts_completion_with_facts_only():
    action = _complete_action()
    state = _complete_run_state(agent_outputs=[])
    context = build_orchestration_planner_context(
        run_state=state,
        candidate_scope=["agent-1"],
        message_text="Summarize the collected facts",
    )
    adapter = RoomSupervisorPlannerAdapter(raw_action_provider=lambda _context: action)

    result = await adapter.plan(context)

    assert result is action


@pytest.mark.asyncio
async def test_planner_adapter_accepts_complete_with_facts_only():
    action = PlannerAction(
        action=PlannerActionType.COMPLETE,
        reasoning="Synthesize the collected facts.",
    )
    state = _complete_run_state(agent_outputs=[])
    context = build_orchestration_planner_context(
        run_state=state,
        candidate_scope=["agent-1"],
        message_text="Synthesize the collected facts",
    )
    adapter = RoomSupervisorPlannerAdapter(raw_action_provider=lambda _context: action)

    assert await adapter.plan(context) is action


@pytest.mark.asyncio
async def test_planner_adapter_rejects_completion_with_snapshot_only():
    action = _complete_action(
        referenced_fact_ids=[],
        referenced_artifact_keys=[],
    )
    state = _complete_run_state(
        agent_outputs=[],
        facts=[],
        artifacts=[],
        participant_snapshot=ParticipantSnapshot(
            mode="debate",
            ordered_agent_ids=["agent-1"],
            max_rounds=1,
            turn_policy="debate_rounds",
        ),
    )
    context = build_orchestration_planner_context(
        run_state=state,
        candidate_scope=["agent-1"],
        message_text="Complete before the debate starts",
    )
    adapter = RoomSupervisorPlannerAdapter(raw_action_provider=lambda _context: action)

    with pytest.raises(PlannerActionValidationError, match="requires agent output"):
        await adapter.plan(context)


@pytest.mark.parametrize(
    ("provider_action", "planner_action"),
    [
        ("clarify", PlannerActionType.ASK_USER),
        ("done", PlannerActionType.COMPLETE),
        ("delegate", PlannerActionType.DELEGATE),
        ("synthesize", PlannerActionType.COMPLETE),
    ],
)
def test_orchestration_adapter_maps_provider_action_aliases(
    provider_action, planner_action
):
    action = RoomSupervisorService._parse_provider_action_as_planner_action(
        {
            "action": provider_action,
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


def test_orchestration_adapter_unknown_action_raises():
    with pytest.raises(ValueError, match="unknown planner action"):
        RoomSupervisorService._parse_provider_action_as_planner_action(
            {
                "action": "mystery",
                "reasoning": "test",
            }
        )


def test_orchestration_adapter_missing_action_raises():
    with pytest.raises(ValueError, match="action"):
        RoomSupervisorService._parse_provider_action_as_planner_action(
            {
                "reasoning": "test",
            }
        )


def test_orchestration_adapter_rejects_non_list_targets_when_present():
    with pytest.raises(ValueError, match="targets"):
        RoomSupervisorService._parse_provider_action_as_planner_action(
            {
                "action": "delegate",
                "reasoning": "test",
                "targets": "",
            }
        )


def test_orchestration_adapter_rejects_non_object_target():
    with pytest.raises(ValueError, match="target"):
        RoomSupervisorService._parse_provider_action_as_planner_action(
            {
                "action": "delegate",
                "reasoning": "test",
                "targets": ["not an object"],
            }
        )


def test_orchestration_adapter_rejects_delegate_target_missing_agent_id():
    with pytest.raises(ValueError, match="agent_id"):
        RoomSupervisorService._parse_provider_action_as_planner_action(
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
def test_orchestration_adapter_rejects_delegate_target_missing_or_empty_task(
    task_value,
):
    target = {
        "agent_id": "agent-1",
        "agent_name": "Agent One",
    }
    if task_value is not None:
        target["task"] = task_value

    with pytest.raises(ValueError, match="task"):
        RoomSupervisorService._parse_provider_action_as_planner_action(
            {
                "action": "delegate",
                "reasoning": "test",
                "targets": [target],
            }
        )


def _guardrail_target(agent_id="agent-1", repair_of=None):
    ref = DispatchContentRef(
        kind=DispatchRefKind.CONTEXT,
        ref_id="ctx-1",
        source_agent_message_id="i1:message",
    )
    return PlannedDelegateTarget(
        agent_id=agent_id,
        task="produce quote",
        context_refs=[ref],
        repair_of_intent_id=repair_of,
        expected_outputs=[
            DispatchExpectedOutput(
                output_key="quote",
                kind="artifact",
                artifact_name="quote",
                required=True,
            )
        ],
    )


def _guardrail_action(targets):
    return PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="delegate",
        targets=list(targets),
    )


def _guardrail_state(
    *,
    outcomes=None,
    intents=None,
    failures=None,
    blockers=None,
    open_questions=None,
):
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="produce quote",
        candidate_agent_ids=["agent-1", "agent-2"],
        delegation_outcomes=list(outcomes or []),
        dispatch_intents=list(intents or []),
        open_failures=list(failures or []),
        blockers=list(blockers or []),
        open_questions=list(open_questions or []),
    )


def _validation_code(action, state):
    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=True,
            resource_fingerprints={},
        )
    return exc_info.value.code


def test_disabled_guardrails_skip_retry_policy_evaluation(monkeypatch):
    action = _guardrail_action([_guardrail_target()])

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("disabled guardrails must not evaluate retry policy")

    monkeypatch.setattr(
        "execution.orchestration.action_validator.evaluate_retry",
        fail_if_called,
    )

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=_guardrail_state(),
            guardrails_enabled=False,
            resource_fingerprints={},
        )
        is action
    )


def test_duplicate_delegate_pair_rejected_before_intents_exist():
    targets = [
        _guardrail_target().model_copy(update={"parallel_group": "parallel-1"}),
        _guardrail_target().model_copy(update={"parallel_group": "parallel-1"}),
    ]
    action = _guardrail_action(targets)
    assert _validation_code(action, _guardrail_state()) == (
        "duplicate_delegate_goal_target"
    )


def test_delegate_structural_errors_precede_outcome_policy_when_guardrails_enabled():
    action = _guardrail_action([_guardrail_target(), _guardrail_target()])

    assert _validation_code(action, _guardrail_state()) == (
        "duplicate_delegate_goal_target"
    )


def test_fulfilled_revision_repeat_is_rejected():
    fingerprints = PlannerActionValidator._target_goal_fingerprints(
        _guardrail_target(), {}
    )
    outcome = DelegationOutcomeRecord(
        outcome_id="o1",
        dispatch_intent_id="i1",
        agent_id="agent-1",
        goal_family_fingerprint=fingerprints.goal_family_fingerprint,
        goal_revision_fingerprint=fingerprints.goal_revision_fingerprint,
        attempt_fingerprint="attempt-1",
        status="fulfilled",
    )
    state = _guardrail_state(outcomes=[outcome])
    assert _validation_code(_guardrail_action([_guardrail_target()]), state) == (
        "delegate_goal_already_fulfilled"
    )


def test_fulfilled_revision_repeat_is_allowed_when_guardrails_are_disabled():
    fingerprints = PlannerActionValidator._target_goal_fingerprints(
        _guardrail_target(), {}
    )
    outcome = DelegationOutcomeRecord(
        outcome_id="o1",
        dispatch_intent_id="i1",
        agent_id="agent-1",
        goal_family_fingerprint=fingerprints.goal_family_fingerprint,
        goal_revision_fingerprint=fingerprints.goal_revision_fingerprint,
        attempt_fingerprint="attempt-1",
        status="fulfilled",
    )
    action = _guardrail_action([_guardrail_target()])

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=_guardrail_state(outcomes=[outcome]),
            resource_fingerprints={},
        )
        is action
    )


def test_exhausted_failed_retry_is_rejected_without_repair_lineage():
    fingerprints = PlannerActionValidator._target_goal_fingerprints(
        _guardrail_target(), {}
    )
    intent = DispatchIntent(
        step_id="i1",
        step_target_id="i1:target",
        dispatch_intent_id="i1",
        planned_agent_message_id="i1:message",
        agent_id="agent-1",
        task="produce quote",
        task_hash="task-hash",
        status="failed",
        context_refs=_guardrail_target().context_refs,
    )
    outcome = DelegationOutcomeRecord(
        outcome_id="o1",
        dispatch_intent_id="i1",
        agent_id="agent-1",
        goal_family_fingerprint=fingerprints.goal_family_fingerprint,
        goal_revision_fingerprint=fingerprints.goal_revision_fingerprint,
        attempt_fingerprint="attempt-1",
        status="failed",
        remaining_required_obligations=["quote:$present"],
        open_failure_ids=["f1"],
    )
    failure = OpenFailureRecord(
        failure_id="f1",
        fingerprint="runtime-failure",
        source="runtime",
        agent_id="agent-1",
        agent_message_id="i1:message",
        dispatch_intent_id="i1",
        error_code="transport_error",
        error_message="connection reset",
        recoverable=True,
        retry_count=2,
        max_retries=2,
    )
    state = _guardrail_state(outcomes=[outcome], intents=[intent], failures=[failure])
    assert _validation_code(_guardrail_action([_guardrail_target()]), state) == (
        "recovery_retry_exhausted"
    )


def test_failed_retry_with_remaining_budget_is_allowed_without_repair_lineage():
    fingerprints = PlannerActionValidator._target_goal_fingerprints(
        _guardrail_target(), {}
    )
    intent = DispatchIntent(
        step_id="i1",
        step_target_id="i1:target",
        dispatch_intent_id="i1",
        planned_agent_message_id="i1:message",
        agent_id="agent-1",
        task="produce quote",
        task_hash="task-hash",
        status="failed",
        context_refs=_guardrail_target().context_refs,
    )
    outcome = DelegationOutcomeRecord(
        outcome_id="o1",
        dispatch_intent_id="i1",
        agent_id="agent-1",
        goal_family_fingerprint=fingerprints.goal_family_fingerprint,
        goal_revision_fingerprint=fingerprints.goal_revision_fingerprint,
        attempt_fingerprint="attempt-1",
        status="failed",
        remaining_required_obligations=["quote:$present"],
        open_failure_ids=["f1"],
    )
    failure = OpenFailureRecord(
        failure_id="f1",
        fingerprint="runtime-failure",
        source="runtime",
        agent_id="agent-1",
        agent_message_id="i1:message",
        dispatch_intent_id="i1",
        error_code="transport_error",
        error_message="connection reset",
        recoverable=True,
        retry_count=0,
        max_retries=2,
    )
    state = _guardrail_state(outcomes=[outcome], intents=[intent], failures=[failure])
    action = _guardrail_action([_guardrail_target()])

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=True,
            resource_fingerprints={},
        )
        is action
    )


def test_no_progress_repeat_is_rejected_for_same_attempt_chain():
    fingerprints = PlannerActionValidator._target_goal_fingerprints(
        _guardrail_target(), {}
    )
    intent = DispatchIntent(
        step_id="i1",
        step_target_id="i1:target",
        dispatch_intent_id="i1",
        planned_agent_message_id="i1:message",
        agent_id="agent-1",
        task="produce quote",
        task_hash="task-hash",
        context_refs=_guardrail_target().context_refs,
        repair_of_intent_id="i0",
    )
    outcome = DelegationOutcomeRecord(
        outcome_id="o1",
        dispatch_intent_id="i1",
        agent_id="agent-1",
        goal_family_fingerprint=fingerprints.goal_family_fingerprint,
        goal_revision_fingerprint=fingerprints.goal_revision_fingerprint,
        attempt_fingerprint="attempt-1",
        status="no_progress",
        remaining_required_obligations=["quote:$present"],
    )
    state = _guardrail_state(outcomes=[outcome], intents=[intent])

    assert (
        _validation_code(_guardrail_action([_guardrail_target(repair_of="i1")]), state)
        == "delegate_no_progress_repeat"
    )


def test_unresolved_revision_allows_an_alternate_agent_attempt_chain():
    fingerprints = PlannerActionValidator._target_goal_fingerprints(
        _guardrail_target(), {}
    )
    intent = DispatchIntent(
        step_id="i1",
        step_target_id="i1:target",
        dispatch_intent_id="i1",
        planned_agent_message_id="i1:message",
        agent_id="agent-1",
        task="produce quote",
        task_hash="task-hash",
        context_refs=_guardrail_target().context_refs,
        repair_of_intent_id="i0",
    )
    outcome = DelegationOutcomeRecord(
        outcome_id="o1",
        dispatch_intent_id="i1",
        agent_id="agent-1",
        goal_family_fingerprint=fingerprints.goal_family_fingerprint,
        goal_revision_fingerprint=fingerprints.goal_revision_fingerprint,
        attempt_fingerprint="attempt-1",
        status="no_progress",
        remaining_required_obligations=["quote:$present"],
    )
    state = _guardrail_state(outcomes=[outcome], intents=[intent])
    action = _guardrail_action([_guardrail_target(agent_id="agent-2")])

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=True,
            resource_fingerprints={},
        )
        is action
    )


def test_normalized_semantic_repair_passes_delegate_lineage_guardrail():
    from execution.orchestration.goal_fingerprinting import target_goal_fingerprints
    from execution.orchestration.recovery_policy import (
        normalize_delegate_repair_lineage,
    )

    target = _guardrail_target()
    fingerprints = target_goal_fingerprints(target, {})
    intent = DispatchIntent(
        step_id="i1",
        step_target_id="i1:target",
        dispatch_intent_id="i1",
        planned_agent_message_id="i1:message",
        agent_id="agent-1",
        task="produce quote",
        task_hash="task-hash",
        goal_family_fingerprint=fingerprints.goal_family_fingerprint,
        goal_revision_fingerprint=fingerprints.goal_revision_fingerprint,
        context_refs=target.context_refs,
    )
    outcome = DelegationOutcomeRecord(
        outcome_id="o1",
        dispatch_intent_id="i1",
        agent_id="agent-1",
        goal_family_fingerprint=fingerprints.goal_family_fingerprint,
        goal_revision_fingerprint=fingerprints.goal_revision_fingerprint,
        attempt_fingerprint="attempt-1",
        status="partial",
        remaining_required_obligations=["quote:$present"],
    )
    state = _guardrail_state(outcomes=[outcome], intents=[intent])
    action = normalize_delegate_repair_lineage(_guardrail_action([target]), state, {})

    assert action.targets[0].repair_of_intent_id == "i1"
    assert (
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=True,
            resource_fingerprints={},
        )
        is action
    )


def test_fail_rejected_when_goal_already_satisfied():
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="travel and weather",
        candidate_agent_ids=["agent-1", "agent-2"],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="msg-travel",
                agent_id="agent-1",
                status="completed",
                text="Hawaii itinerary",
            ),
            AgentOutputRecord(
                agent_message_id="msg-weather",
                agent_id="agent-2",
                status="completed",
                text="Current weather only",
            ),
        ],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="o1",
                dispatch_intent_id="i1",
                agent_id="agent-1",
                goal_family_fingerprint="f1",
                goal_revision_fingerprint="r1",
                attempt_fingerprint="a1",
                status="fulfilled",
            ),
            DelegationOutcomeRecord(
                outcome_id="o2",
                dispatch_intent_id="i2",
                agent_id="agent-2",
                goal_family_fingerprint="f2",
                goal_revision_fingerprint="r2",
                attempt_fingerprint="a2",
                status="fulfilled",
            ),
        ],
        goal_progress=[],
    )
    action = PlannerAction(
        action=PlannerActionType.FAIL,
        reasoning="weather history unavailable",
        failure_reason="Weather Agent cannot provide historical data",
    )

    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(action, run_state=state)
    assert exc_info.value.code == "fail_goal_already_satisfied"


def test_fail_allowed_when_required_output_key_still_missing():
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="story and summary",
        candidate_agent_ids=["story", "summarizer"],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="msg-story",
                agent_id="story",
                status="completed",
                text="Once upon a time...",
            )
        ],
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="story-intent",
                planned_agent_message_id="msg-story",
                agent_id="story",
                task="Write a story",
                task_hash="hash-story",
                expected_outputs=[
                    DispatchExpectedOutput(
                        output_key="story_text",
                        kind="text",
                        required=True,
                    ),
                    DispatchExpectedOutput(
                        output_key="summary",
                        kind="text",
                        required=True,
                    ),
                ],
                status="success",
            )
        ],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="o1",
                dispatch_intent_id="story-intent",
                agent_id="story",
                goal_family_fingerprint="f1",
                goal_revision_fingerprint="r1",
                attempt_fingerprint="a1",
                status="fulfilled",
                satisfied_output_keys=["story_text"],
                missing_output_keys=["summary"],
            )
        ],
        goal_progress=[],
    )
    action = PlannerAction(
        action=PlannerActionType.FAIL,
        reasoning="summary unavailable",
        failure_reason="Summarizer could not produce required summary output",
    )

    validated = PlannerActionValidator.validate(action, run_state=state)

    assert validated.action == PlannerActionType.FAIL


def test_parallel_delegate_rejects_same_step_context_dependency():
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="story and image",
        candidate_agent_ids=["story", "image"],
    )
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="fanout",
        targets=[
            PlannedDelegateTarget(
                agent_id="story",
                task="Write a story",
                parallel_group="fanout-1",
                expected_outputs=[
                    DispatchExpectedOutput(
                        output_key="story_text",
                        kind="text",
                        required=True,
                    )
                ],
            ),
            PlannedDelegateTarget(
                agent_id="image",
                task="Generate an image from the story",
                parallel_group="fanout-1",
                context_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.CONTEXT,
                        ref_id="story_text",
                    )
                ],
                expected_outputs=[
                    DispatchExpectedOutput(
                        output_key="image",
                        kind="image/png",
                        required=True,
                    )
                ],
            ),
        ],
    )

    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(
            action,
            run_state=state,
            candidate_agent_ids=["story", "image"],
        )
    assert exc_info.value.code == "parallel_context_dependency"


def test_delegate_accepts_rewritten_story_context_ref():
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="story and image",
        candidate_agent_ids=["story", "image"],
        facts=[
            {
                "fact_id": "story-msg:text_evidence",
                "kind": "agent_text_evidence",
                "value": "Once upon a time",
                "source_agent_message_id": "story-msg",
            }
        ],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="story-msg",
                agent_id="story",
                status="completed",
                text="Once upon a time",
            )
        ],
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:t1",
                dispatch_intent_id="story-intent",
                planned_agent_message_id="story-msg",
                agent_id="story",
                task="Write a story",
                task_hash="hash",
                expected_outputs=[
                    DispatchExpectedOutput(
                        output_key="story_text",
                        kind="text",
                        required=True,
                    )
                ],
                status="success",
            )
        ],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="o1",
                dispatch_intent_id="story-intent",
                agent_id="story",
                goal_family_fingerprint="f1",
                goal_revision_fingerprint="r1",
                attempt_fingerprint="a1",
                status="fulfilled",
                satisfied_output_keys=["story_text"],
            )
        ],
    )
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="image from story",
        targets=[
            PlannedDelegateTarget(
                agent_id="image",
                task="Generate an image based on the story",
                context_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.CONTEXT,
                        ref_id="story_text",
                        source_agent_message_id="story-msg",
                    )
                ],
                expected_outputs=[
                    DispatchExpectedOutput(
                        output_key="image",
                        kind="image/png",
                        required=True,
                    )
                ],
            )
        ],
    )

    validated = PlannerActionValidator.validate(
        action,
        run_state=state,
        candidate_agent_ids=["story", "image"],
    )
    assert validated is action
