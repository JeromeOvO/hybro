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

    with pytest.raises(
        PlannerActionValidationError,
        match="step budget",
    ) as delegate_error:
        _validate(delegate, steps_used=8, step_budget=8)
    assert delegate_error.value.code == "step_budget_exhausted"
    assert delegate_error.value.recoverable is False

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


def test_multi_target_delegate_without_parallel_group_is_rejected():
    action = _action(
        PlannerActionType.DELEGATE,
        targets=[
            _target(agent_id="agent-1"),
            _target(agent_id="agent-2"),
        ],
    )

    with pytest.raises(PlannerActionValidationError) as exc_info:
        _validate(action, candidate_agent_ids=["agent-1", "agent-2"])

    assert exc_info.value.code == "parallel_dependency_unspecified"


@pytest.mark.parametrize("parallel_group", ["", "  "])
def test_multi_target_delegate_with_blank_parallel_group_is_rejected(
    parallel_group,
):
    action = _action(
        PlannerActionType.DELEGATE,
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Summarize section A.",
                parallel_group=parallel_group,
            ),
            PlannedDelegateTarget(
                agent_id="agent-2",
                task="Summarize section B.",
                parallel_group=parallel_group,
            ),
        ],
    )

    with pytest.raises(PlannerActionValidationError) as exc_info:
        _validate(action, candidate_agent_ids=["agent-1", "agent-2"])

    assert exc_info.value.code == "parallel_dependency_unspecified"


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

    assert _validation_code(action, _guardrail_state()) == "duplicate_question_in_action"


def test_post_dispatch_question_requires_blocker_keys():
    state = _guardrail_state(intents=[_completed_quote_intent()])

    assert _validation_code(_question_action("blocker", []), state) == (
        "ask_user_blocker_keys_required"
    )


def test_post_dispatch_question_rejects_candidate_only_blocker():
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
    adapter = RoomSupervisorPlannerAdapter(raw_action_provider=lambda _context: raw_action)

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
    assert target.expected_outputs == [
        DispatchExpectedOutput(
            kind="quote_summary",
            required=True,
            description="Summarize underwriting blockers.",
        )
    ]
    assert target.attachment_policy == "compatible_only"


def test_planner_schema_does_not_expose_attachment_policy():
    target_schema = PLANNER_ACTION_RESPONSE_SCHEMA["properties"]["targets"]["items"]

    assert "attachment_policy" not in target_schema["properties"]
    assert "attachment_policy" not in target_schema["required"]


def test_planner_schema_requires_v2_outcome_policy_fields():
    target_schema = PLANNER_ACTION_RESPONSE_SCHEMA["properties"]["targets"]["items"]
    expected_output_schema = target_schema["properties"]["expected_outputs"]["items"]
    question_schema = PLANNER_ACTION_RESPONSE_SCHEMA["properties"]["questions"]["items"]

    assert set(target_schema["required"]) == {
        "agent_id",
        "agent_name",
        "task",
        "parallel_group",
        "depends_on",
        "required_resource_refs",
        "context_refs",
        "artifact_refs",
        "attachment_refs",
        "expected_outputs",
        "repair_of_intent_id",
    }
    assert set(expected_output_schema["required"]) == {
        "output_key",
        "kind",
        "required",
        "description",
        "artifact_name",
        "required_fields",
        "allow_partial",
    }
    assert set(question_schema["required"]) == {
        "prompt",
        "prompt_type",
        "choices",
        "reason",
        "blocker_keys",
    }
    with pytest.raises(ValidationError, match="repair_of_intent_id"):
        validate(
            {
                "action": "delegate",
                "reasoning": "Use the selected specialist.",
                "targets": [
                    {
                        "agent_id": "agent-1",
                        "agent_name": "Agent One",
                        "task": "Provide a summary.",
                        "parallel_group": None,
                        "depends_on": [],
                        "required_resource_refs": [],
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
        },
    }

    validate(payload, PLANNER_ACTION_RESPONSE_SCHEMA)

    action = RoomSupervisorPlannerAdapter()._parse_action(payload)

    assert action.completion_evidence.satisfied_output_keys == ["quote"]
    assert action.completion_evidence.waived_outputs[0].reason == (
        "The user did not request the addendum."
    )


def test_completion_validator_rejects_constructed_blank_disposition_reason():
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

    with pytest.raises(PlannerActionValidationError, match="nonempty") as exc_info:
        PlannerActionValidator.validate(action, run_state=_state_for_validation())

    assert exc_info.value.code == "completion_disposition_request_invalid"


def test_completion_validator_rejects_requested_unknown_disposition_revision():
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

    assert _validation_code(action, state) == "completion_disposition_unreferenced"


def test_legacy_planner_parser_defaults_absent_outcome_policy_fields():
    action = RoomSupervisorService._parse_legacy_action_as_planner_action(
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
    adapter = RoomSupervisorPlannerAdapter(raw_action_provider=lambda _context: raw_action)

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
    target_schema = supervisor_service.schema["properties"]["targets"]["items"]
    expected_outputs_schema = target_schema["properties"]["expected_outputs"]
    assert expected_outputs_schema["items"]["type"] == "object"
    assert "kind" in expected_outputs_schema["items"]["required"]
    dependency_fields = {
        "parallel_group",
        "depends_on",
        "required_resource_refs",
    }
    assert dependency_fields <= target_schema["properties"].keys()
    assert dependency_fields <= set(target_schema["required"])
    assert "shared non-null parallel_group" in supervisor_service.system_prompt
    assert (
        "output_key, kind, required, description, artifact_name, required_fields, "
        "and allow_partial"
    ) in supervisor_service.system_prompt


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
        action=PlannerActionType.SYNTHESIZE,
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
        ("active_missing_obligation", "completion_required_output_missing"),
        ("unreferenced_disposition", "completion_disposition_unreferenced"),
        ("open_runtime_failure", "completion_open_failure"),
        ("pending_hitl", "completion_pending_hitl"),
        ("validated_open_blocker", "completion_open_blocker"),
    ],
)
def test_completion_scope_rejections(case, expected_code):
    action, state = _completion_case(case)

    assert _validation_code(action, state) == expected_code


@pytest.mark.parametrize(
    ("action", "expected_code"),
    [
        (_synthesize_action(), "completion_blocked_by_recoverable_failure"),
        (_complete_action(), "completion_open_failure"),
    ],
)
def test_open_runtime_failures_only_block_terminal_actions_when_guardrails_enabled(
    action,
    expected_code,
):
    _, state = _completion_case("open_runtime_failure")

    assert (
        PlannerActionValidator.validate(
            action,
            run_state=state,
            guardrails_enabled=False,
            resource_fingerprints={},
        )
        is action
    )
    assert _validation_code(action, state) == expected_code


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


@pytest.mark.parametrize("status", ["abandoned", "expired"])
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


def test_complete_requires_structured_evidence():
    action = PlannerAction(action=PlannerActionType.COMPLETE, reasoning="done")

    with pytest.raises(PlannerActionValidationError, match="completion evidence"):
        PlannerActionValidator.validate(action, run_state=_complete_run_state())


def test_complete_rejects_unknown_fact_reference():
    action = _complete_action(referenced_fact_ids=["missing-fact"])

    with pytest.raises(PlannerActionValidationError, match="missing-fact"):
        PlannerActionValidator.validate(action, run_state=_complete_run_state())


def test_complete_rejects_pending_hitl_and_active_dispatches():
    action = _complete_action()

    with pytest.raises(PlannerActionValidationError, match="pending HITL"):
        PlannerActionValidator.validate(
            action,
            run_state=_complete_run_state(pending_hitl_request_ids=["hitl-1"]),
        )

    with pytest.raises(PlannerActionValidationError, match="active dispatch") as exc_info:
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


def test_synthesize_allows_open_planner_validation_failure():
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
    action = _synthesize_action()

    assert PlannerActionValidator.validate(action, run_state=state) is action


def test_complete_allows_abandoned_recoverable_failure():
    state = _complete_run_state(open_failures=[_failure("abandoned")])

    action = _complete_action()

    assert PlannerActionValidator.validate(action, run_state=state) is action


def test_synthesize_rejected_when_recoverable_failure_is_open():
    state = _complete_run_state(open_failures=[_failure("open")])

    with pytest.raises(PlannerActionValidationError, match="open recoverable failure"):
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
            run_state=_complete_run_state(
                open_questions=[open_question]
            ),
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


def test_complete_rejects_blank_satisfied_criteria():
    action = _complete_action(satisfied_criteria=["  "])

    with pytest.raises(PlannerActionValidationError, match="satisfied criteria"):
        PlannerActionValidator.validate(action, run_state=_complete_run_state())


def test_complete_accepts_valid_evidence():
    action = _complete_action()

    assert PlannerActionValidator.validate(action, run_state=_complete_run_state()) is action


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
async def test_planner_adapter_rejects_synthesis_with_facts_only():
    action = PlannerAction(
        action=PlannerActionType.SYNTHESIZE,
        reasoning="Synthesize the collected facts.",
    )
    state = _complete_run_state(agent_outputs=[])
    context = build_orchestration_planner_context(
        run_state=state,
        candidate_scope=["agent-1"],
        message_text="Synthesize the collected facts",
    )
    adapter = RoomSupervisorPlannerAdapter(raw_action_provider=lambda _context: action)

    with pytest.raises(PlannerActionValidationError, match="requires agent output"):
        await adapter.plan(context)


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
        "parallel_dependency_unspecified"
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

    assert PlannerActionValidator.validate(
        action,
        run_state=_guardrail_state(outcomes=[outcome]),
        resource_fingerprints={},
    ) is action


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
    state = _guardrail_state(
        outcomes=[outcome], intents=[intent], failures=[failure]
    )
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
    state = _guardrail_state(
        outcomes=[outcome], intents=[intent], failures=[failure]
    )
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

    assert _validation_code(
        _guardrail_action([_guardrail_target(repair_of="i1")]), state
    ) == "delegate_no_progress_repeat"


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
