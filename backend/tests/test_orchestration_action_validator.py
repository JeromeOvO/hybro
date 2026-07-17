import pytest

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
    CandidateAgentSnapshot,
    CandidateScopeSnapshot,
    CompletionEvidence,
    DispatchContentRef,
    DispatchExpectedOutput,
    DispatchRefKind,
    OpenFailureRecord,
    OrchestrationRunState,
    ParticipantSnapshot,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
)


def _state_for_validation() -> OrchestrationRunState:
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="message-1",
        goal="Coordinate this",
        candidate_agent_ids=["agent-1"],
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

    with pytest.raises(PlannerActionValidationError, match="active dispatch"):
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

    with pytest.raises(PlannerActionValidationError, match="open recoverable failure"):
        PlannerActionValidator.validate(_complete_action(), run_state=state)


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
        PlannerActionValidator.validate(_synthesize_action(), run_state=state)


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
