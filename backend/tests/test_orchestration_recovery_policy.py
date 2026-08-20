import pytest

from execution.orchestration.action_validator import PlannerActionValidator
from execution.orchestration.blocker_resolver import validate_hitl_answered_blockers
from execution.orchestration.goal_fingerprinting import target_goal_fingerprints
from execution.orchestration.goal_progress import rebuild_goal_progress
from execution.orchestration.outcome_evaluator import required_obligations
from execution.orchestration.recovery_policy import (
    action_for_fulfilled_goal_recovery,
    action_for_rejected_ask_user,
    action_for_rejected_delegate,
    normalize_delegate_repair_lineage,
    normalize_independent_parallel_group,
    normalize_prose_expected_outputs,
    recovery_directives,
)
from models.orchestration import (
    AgentOutputRecord,
    BlockerRecord,
    DelegationOutcomeRecord,
    DispatchExpectedOutput,
    DispatchIntent,
    OrchestrationRunState,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
    PlannerQuestion,
)


def _target():
    return PlannedDelegateTarget(
        agent_id="agent-1",
        task="Produce quote.",
        expected_outputs=[
            DispatchExpectedOutput(output_key="quote", kind="artifact", required=True)
        ],
    )


def _state(*, status="partial", goal_revision_fingerprint=None):
    expected_outputs = [
        DispatchExpectedOutput(output_key="quote", kind="artifact", required=True)
    ]
    fingerprints = target_goal_fingerprints(_target(), {})
    goal_revision_fingerprint = (
        fingerprints.goal_revision_fingerprint
        if goal_revision_fingerprint is None
        else goal_revision_fingerprint
    )
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="produce quote",
        candidate_agent_ids=["agent-1"],
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Produce quote.",
                task_hash="hash-1",
                goal_family_fingerprint=fingerprints.goal_family_fingerprint,
                goal_revision_fingerprint=goal_revision_fingerprint,
                expected_outputs=expected_outputs,
            )
        ],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="agent-1",
                goal_family_fingerprint=fingerprints.goal_family_fingerprint,
                goal_revision_fingerprint=goal_revision_fingerprint,
                attempt_fingerprint="attempt-1",
                status=status,
                remaining_required_obligations=["quote:$present"],
            )
        ],
    )


def test_normalizes_missing_repair_of_intent_for_same_agent_unfulfilled_revision():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="repair",
        targets=[_target()],
    )

    normalized = normalize_delegate_repair_lineage(action, _state(), {})

    assert normalized.targets[0].repair_of_intent_id == "intent-1"
    assert action.targets[0].repair_of_intent_id is None


def test_recovery_directives_prefer_validated_blocker_question():
    state = _state()
    state.blockers = [
        BlockerRecord(
            key="blocker-1",
            description="Need requested limit.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        )
    ]

    directives = recovery_directives(state)

    assert directives == [
        {
            "code": "ask_user_for_validated_blocker",
            "blocker_keys": ["blocker-1"],
            "reason": "Validated user-only blocker is open.",
        }
    ]


def test_rejected_delegate_falls_back_to_ask_user_for_validated_blocker():
    state = _state()
    state.delegation_outcomes[-1].remaining_required_obligations = [
        "quote:requested_limit"
    ]
    state.blockers = [
        BlockerRecord(
            key="blocker-1",
            description="Need requested limit.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        )
    ]

    action = action_for_rejected_delegate(
        state,
        error_code="delegate_blocked_pending_user",
    )

    assert action is not None
    assert action.action == PlannerActionType.ASK_USER
    assert action.questions == [
        PlannerQuestion(
            prompt="Need requested limit.",
            reason="blocker",
            blocker_keys=["blocker-1"],
            required_obligation_keys=["quote:requested_limit"],
            blocker_obligations={"blocker-1": ["quote:requested_limit"]},
        )
    ]


def test_rejected_delegate_maps_obligations_from_structured_blocker_fields():
    state = _state()
    state.delegation_outcomes[-1].remaining_required_obligations = [
        "quote:industry",
        "quote:requested_limit",
    ]
    state.blockers = [
        BlockerRecord(
            key="agent_blocker:agent-1:client.industry",
            description="Need industry and requested limit.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        ),
        BlockerRecord(
            key="agent_blocker:agent-1:requested_coverage.limit",
            description="Need industry and requested limit.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        ),
    ]

    action = action_for_rejected_delegate(
        state,
        error_code="delegate_blocked_pending_user",
    )

    assert action is not None
    assert action.questions[0].blocker_obligations == {
        "agent_blocker:agent-1:client.industry": ["quote:industry"],
        "agent_blocker:agent-1:requested_coverage.limit": ["quote:requested_limit"],
    }


def test_rejected_delegate_preserves_presence_only_obligation():
    state = _state()
    state.blockers = [
        BlockerRecord(
            key="agent_blocker:agent-1:quote",
            description="Need the quote.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        )
    ]

    action = action_for_rejected_delegate(
        state,
        error_code="delegate_blocked_pending_user",
    )

    assert action is not None
    assert action.questions[0].blocker_obligations == {
        "agent_blocker:agent-1:quote": ["quote:$present"]
    }


def test_rejected_ask_user_completes_when_agent_results_fulfilled():
    state = _state(status="fulfilled")
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="msg-1",
            agent_id="agent-1",
            status="completed",
            text="Here is your Hawaii itinerary.",
        )
    ]
    state.blockers = []

    action = action_for_rejected_ask_user(
        state,
        error_code="ask_user_blocker_keys_required",
    )

    assert action is not None
    assert action.action == PlannerActionType.COMPLETE
    assert action.completion_evidence is None


def test_rejected_ask_user_prefers_validated_blocker_hitl():
    state = _state(status="fulfilled")
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="msg-1",
            agent_id="agent-1",
            status="completed",
            text="partial answer",
        )
    ]
    state.blockers = [
        BlockerRecord(
            key="blocker-1",
            description="Need travel dates.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        )
    ]

    action = action_for_rejected_ask_user(
        state,
        error_code="ask_user_blocker_not_validated",
    )

    assert action is not None
    assert action.action == PlannerActionType.ASK_USER
    assert action.questions[0].blocker_keys == ["blocker-1"]


def test_rejected_ask_user_without_progress_returns_none():
    state = _state(status="no_progress")
    state.delegation_outcomes = []
    state.agent_outputs = []
    state.blockers = []

    assert (
        action_for_rejected_ask_user(
            state,
            error_code="ask_user_blocker_keys_required",
        )
        is None
    )


def test_rejected_ask_user_with_partial_outcome_and_completed_text_returns_none():
    """Partial/no_progress outcome with completed text must not trigger COMPLETE."""
    state = _state(status="partial")
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="msg-1",
            agent_id="agent-1",
            status="completed",
            text="Here is a partial answer.",
        )
    ]
    state.blockers = []

    assert (
        action_for_rejected_ask_user(
            state,
            error_code="ask_user_blocker_keys_required",
        )
        is None
    )


def test_rejected_ask_user_with_no_progress_outcome_and_artifacts_returns_none():
    """no_progress outcome with artifact keys must not trigger COMPLETE."""
    state = _state(status="no_progress")
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="msg-1",
            agent_id="agent-1",
            status="completed",
            text="",
            artifact_keys=["artifact-1"],
        )
    ]
    state.blockers = []

    assert (
        action_for_rejected_ask_user(
            state,
            error_code="ask_user_blocker_keys_required",
        )
        is None
    )


@pytest.mark.parametrize(
    "error_code",
    [
        "completion_evidence_invalid",
        "platform_answer_instruction_missing",
    ],
)
def test_fulfilled_goal_recovery_completes_when_agents_fulfilled(error_code: str):
    state = _state(status="fulfilled")
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="msg-story",
            agent_id="agent-story",
            status="completed",
            text="Once upon a time...",
        ),
        AgentOutputRecord(
            agent_message_id="msg-image",
            agent_id="agent-image",
            status="completed",
            text="",
            artifact_keys=["artifact-image-1"],
        ),
    ]
    state.blockers = []

    action = action_for_fulfilled_goal_recovery(state, error_code=error_code)

    assert action is not None
    assert action.action == PlannerActionType.COMPLETE
    assert action.completion_evidence is None


def test_delegate_goal_already_fulfilled_waits_until_exhausted():
    state = _state(status="fulfilled")
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="msg-story",
            agent_id="agent-story",
            status="completed",
            text="Once upon a time...",
        ),
    ]
    state.blockers = []

    assert (
        action_for_fulfilled_goal_recovery(
            state,
            error_code="delegate_goal_already_fulfilled",
            exhausted=False,
        )
        is None
    )

    action = action_for_fulfilled_goal_recovery(
        state,
        error_code="delegate_goal_already_fulfilled",
        exhausted=True,
    )
    assert action is not None
    assert action.action == PlannerActionType.COMPLETE
    assert action.completion_evidence is None


def test_fulfilled_goal_recovery_prefers_validated_blocker_hitl():
    state = _state(status="fulfilled")
    state.blockers = [
        BlockerRecord(
            key="blocker-1",
            description="Need travel dates.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        )
    ]

    action = action_for_fulfilled_goal_recovery(
        state,
        error_code="completion_evidence_invalid",
    )

    assert action is not None
    assert action.action == PlannerActionType.ASK_USER
    assert action.questions[0].blocker_keys == ["blocker-1"]


def test_fulfilled_goal_recovery_without_progress_returns_none():
    state = _state(status="no_progress")
    state.delegation_outcomes = []
    state.agent_outputs = []
    state.blockers = []

    assert (
        action_for_fulfilled_goal_recovery(
            state,
            error_code="platform_answer_instruction_missing",
        )
        is None
    )


def test_fulfilled_goal_recovery_ignores_unrelated_codes():
    state = _state(status="fulfilled")

    assert (
        action_for_fulfilled_goal_recovery(
            state,
            error_code="ask_user_blocker_keys_required",
        )
        is None
    )


def test_does_not_set_repair_lineage_for_failed_operational_retry():
    state = _state(status="failed")
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="retry",
        targets=[_target()],
    )

    normalized = normalize_delegate_repair_lineage(action, state, {})

    assert normalized.targets[0].repair_of_intent_id is None


def test_does_not_set_repair_lineage_for_blocked_user_input():
    state = _state(status="blocked")
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="repeat blocked work",
        targets=[_target()],
    )

    normalized = normalize_delegate_repair_lineage(action, state, {})

    assert normalized.targets[0].repair_of_intent_id is None


def test_hitl_resolution_allows_same_agent_repair_and_fulfilled_progress():
    state = _state(status="blocked")
    state.delegation_outcomes[-1].remaining_required_obligations = [
        "quote:requested_limit"
    ]
    blocker = BlockerRecord(
        key="blocker-1",
        description="Need requested limit.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
        status="open",
    )
    state.blockers = [blocker]
    state.delegation_outcomes[-1].blockers = [blocker.model_copy(deep=True)]
    state.open_questions = [
        {
            "request_id": "hitl-1",
            "status": "resolved",
            "resolved": True,
            "blocker_keys": ["blocker-1"],
            "blocker_obligations": {
                "blocker-1": ["quote:requested_limit"],
            },
        }
    ]

    validate_hitl_answered_blockers(
        state,
        resolved_request_ids={"hitl-1"},
        answer_fact={"fact_id": "hitl-fact-1", "text": "$5M"},
    )
    state = rebuild_goal_progress(state)
    repair = normalize_delegate_repair_lineage(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="Continue with the supplied limit.",
            targets=[_target()],
        ),
        state,
        {},
    )

    assert state.blockers[0].status == "resolved"
    assert state.goal_progress[0].status == "partial"
    assert repair.targets[0].repair_of_intent_id == "intent-1"
    assert (
        PlannerActionValidator.validate(
            repair,
            run_state=state,
            resource_fingerprints={},
            guardrails_enabled=True,
        )
        is repair
    )

    state.delegation_outcomes.append(
        state.delegation_outcomes[-1].model_copy(
            update={
                "outcome_id": "outcome-2",
                "dispatch_intent_id": "intent-2",
                "attempt_fingerprint": "attempt-2",
                "status": "fulfilled",
                "remaining_required_obligations": [],
                "newly_satisfied_required_obligations": ["quote:requested_limit"],
                "blockers": [],
            }
        )
    )

    completed = rebuild_goal_progress(state)

    assert completed.goal_progress[0].status == "fulfilled"
    assert completed.goal_progress[0].remaining_required_obligations == []


def test_normalizes_missing_repair_of_intent_for_no_progress_revision():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="repair no progress",
        targets=[_target()],
    )

    normalized = normalize_delegate_repair_lineage(
        action, _state(status="no_progress"), {}
    )

    assert normalized.targets[0].repair_of_intent_id == "intent-1"


def test_does_not_set_repair_lineage_for_same_shape_different_recorded_revision():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="new goal revision",
        targets=[_target()],
    )

    normalized = normalize_delegate_repair_lineage(
        action,
        _state(goal_revision_fingerprint="historical-revision"),
        {},
    )

    assert normalized.targets[0].repair_of_intent_id is None


def test_normalize_independent_parallel_group_fills_shared_blank_group():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="fan out",
        targets=[
            PlannedDelegateTarget(agent_id="agent-1", task="Plan trip."),
            PlannedDelegateTarget(agent_id="agent-2", task="Check weather."),
        ],
    )

    normalized = normalize_independent_parallel_group(action)

    assert normalized.targets[0].parallel_group == normalized.targets[1].parallel_group
    assert normalized.targets[0].parallel_group
    assert normalized.targets[0].parallel_group.startswith("fanout-")


def test_normalize_independent_parallel_group_preserves_explicit_group():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="fan out",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Plan trip.",
                parallel_group="hawaii",
            ),
            PlannedDelegateTarget(
                agent_id="agent-2",
                task="Check weather.",
                parallel_group="hawaii",
            ),
        ],
    )

    normalized = normalize_independent_parallel_group(action)

    assert [target.parallel_group for target in normalized.targets] == [
        "hawaii",
        "hawaii",
    ]


def test_normalize_independent_parallel_group_unifies_partial_group():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="fan out",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Plan trip.",
                parallel_group="hawaii",
            ),
            PlannedDelegateTarget(
                agent_id="agent-2",
                task="Check weather.",
                parallel_group=None,
            ),
        ],
    )

    normalized = normalize_independent_parallel_group(action)

    assert [target.parallel_group for target in normalized.targets] == [
        "hawaii",
        "hawaii",
    ]


def test_normalize_independent_parallel_group_leaves_conflicting_groups():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="fan out",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Plan trip.",
                parallel_group="a",
            ),
            PlannedDelegateTarget(
                agent_id="agent-2",
                task="Check weather.",
                parallel_group="b",
            ),
        ],
    )

    normalized = normalize_independent_parallel_group(action)

    assert [target.parallel_group for target in normalized.targets] == ["a", "b"]


def test_normalize_independent_parallel_group_skips_dependent_targets():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="sequential",
        targets=[
            PlannedDelegateTarget(agent_id="agent-1", task="Plan trip."),
            PlannedDelegateTarget(
                agent_id="agent-2",
                task="Check weather.",
                depends_on=["agent-1"],
            ),
        ],
    )

    normalized = normalize_independent_parallel_group(action)

    assert [target.parallel_group for target in normalized.targets] == [None, None]


def test_normalize_prose_expected_outputs_keeps_text_and_drops_summary_contracts():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="fan out",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-weather",
                task="Check Hawaii weather for the past month.",
                expected_outputs=[
                    DispatchExpectedOutput(
                        output_key="weather_past_month",
                        kind="text",
                        required=True,
                        allow_partial=False,
                    )
                ],
            ),
            PlannedDelegateTarget(
                agent_id="agent-travel",
                task="Create a 7-day Hawaii plan.",
                expected_outputs=[
                    DispatchExpectedOutput(
                        output_key="7_day_travel_plan",
                        kind="summary",
                        required=True,
                    )
                ],
            ),
        ],
    )

    normalized = normalize_prose_expected_outputs(action)

    assert [output.kind for output in normalized.targets[0].expected_outputs] == [
        "text"
    ]
    assert normalized.targets[1].expected_outputs == []


def test_normalize_prose_expected_outputs_keeps_artifact_contracts():
    structured = DispatchExpectedOutput(
        output_key="quote",
        kind="artifact",
        artifact_name="quote",
        required_fields=["pricing.premium"],
    )
    prose = DispatchExpectedOutput(
        output_key="notes",
        kind="text",
        required=True,
    )
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="mixed",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Produce quote and notes.",
                expected_outputs=[structured, prose],
            )
        ],
    )

    normalized = normalize_prose_expected_outputs(action)

    assert [output.kind for output in normalized.targets[0].expected_outputs] == [
        "artifact",
        "text",
    ]


def test_normalize_prose_expected_outputs_keeps_text_media_and_artifact_kinds():
    outputs = [
        DispatchExpectedOutput(output_key="copy", kind="text"),
        DispatchExpectedOutput(output_key="image", kind="image/png"),
        DispatchExpectedOutput(output_key="file", kind="artifact"),
        DispatchExpectedOutput(output_key="summary", kind="summary"),
        DispatchExpectedOutput(output_key="shape", kind="structured"),
    ]
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="mixed contracts",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Produce supported outputs.",
                expected_outputs=outputs,
            )
        ],
    )

    normalized = normalize_prose_expected_outputs(action)

    assert [output.kind for output in normalized.targets[0].expected_outputs] == [
        "text",
        "image/png",
        "artifact",
    ]


def test_normalize_prose_expected_outputs_clears_invented_structured_contracts():
    invented = DispatchExpectedOutput(
        output_key="travel_plan",
        kind="structured",
        artifact_name="Hawaii_Trip_Itinerary",
        required_fields=["destination", "travel_dates", "budget"],
        required=True,
        allow_partial=False,
    )
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="invented structured contract",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-travel",
                task="Create a Hawaii itinerary.",
                expected_outputs=[invented],
            )
        ],
    )

    normalized = normalize_prose_expected_outputs(action)

    assert normalized.targets[0].expected_outputs == []


def test_normalize_prose_expected_outputs_drops_json_labels_but_keeps_paths():
    broker_fields = [
        "Client",
        "Industry",
        "Headquarters Country",
        "Operating countries",
        "Employees",
        "Annual Revenue",
        "Coverage limit",
        "Retention",
        "Effective Date",
        "MFA",
        "Backups",
        "Security Training",
        "Cloud Providers",
        "Endpoint Detection",
        "Patch Management",
        "Prior Claims",
    ]
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="prepare submission and quote",
        targets=[
            PlannedDelegateTarget(
                agent_id="broker",
                task="Prepare the submission.",
                expected_outputs=[
                    DispatchExpectedOutput(
                        output_key="submission",
                        kind="application/json",
                        required=True,
                        required_fields=broker_fields,
                    )
                ],
            ),
            PlannedDelegateTarget(
                agent_id="insurer",
                task="Return a quote.",
                expected_outputs=[
                    DispatchExpectedOutput(
                        output_key="quote_decision",
                        kind="application/json",
                        required=True,
                        required_fields=[
                            "premium",
                            "pricing.currency",
                            "_id",
                            "2fa_enabled",
                            "PascalCase",
                            "UserProfile.DisplayName",
                            "Coverage limit",
                        ],
                    )
                ],
            ),
        ],
    )

    normalized = normalize_prose_expected_outputs(action)
    renormalized = normalize_prose_expected_outputs(normalized)

    submission = normalized.targets[0].expected_outputs[0]
    quote = normalized.targets[1].expected_outputs[0]
    assert submission.required_fields == []
    assert required_obligations([submission]) == {"submission:$present"}
    assert quote.required_fields == [
        "premium",
        "pricing.currency",
        "_id",
        "2fa_enabled",
        "PascalCase",
        "UserProfile.DisplayName",
    ]
    assert action.targets[0].expected_outputs[0].required_fields == broker_fields
    assert renormalized == normalized


def test_normalize_prose_expected_outputs_leaves_complete_unchanged():
    action = PlannerAction(
        action=PlannerActionType.COMPLETE,
        reasoning="done",
    )

    assert normalize_prose_expected_outputs(action) is action


def test_normalize_prose_expected_outputs_canonicalizes_text_constraints():
    shaped = DispatchExpectedOutput(
        output_key="weather_report",
        kind="text",
        required_fields=["temperature"],
    )
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="shaped text",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Return structured weather fields.",
                expected_outputs=[shaped],
            )
        ],
    )

    normalized = normalize_prose_expected_outputs(action)

    output = normalized.targets[0].expected_outputs[0]
    assert output.kind == "text"
    assert output.artifact_name is None
    assert output.required_fields == []


def test_fail_goal_already_satisfied_recovers_to_complete():
    state = _state(status="fulfilled")
    state.goal_progress = []
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="msg-travel",
            agent_id="agent-1",
            status="completed",
            text="7-day Hawaii itinerary...",
        ),
        AgentOutputRecord(
            agent_message_id="msg-weather",
            agent_id="agent-2",
            status="completed",
            text="I can only provide current weather, not historical data.",
        ),
    ]
    state.delegation_outcomes.append(
        DelegationOutcomeRecord(
            outcome_id="outcome-2",
            dispatch_intent_id="intent-2",
            agent_id="agent-2",
            goal_family_fingerprint="family-weather",
            goal_revision_fingerprint="revision-weather",
            attempt_fingerprint="attempt-2",
            status="fulfilled",
        )
    )
    state.blockers = []

    action = action_for_fulfilled_goal_recovery(
        state,
        error_code="fail_goal_already_satisfied",
    )

    assert action is not None
    assert action.action == PlannerActionType.COMPLETE
    assert action.completion_evidence is None


def test_normalize_context_refs_rewrites_output_key_to_fact_id():
    from execution.orchestration.recovery_policy import (
        normalize_context_refs_with_available_facts,
    )
    from models.orchestration import DispatchContentRef, DispatchRefKind

    state = _state(status="fulfilled")
    state.facts = [
        {
            "fact_id": "agent-msg-1:text_evidence",
            "kind": "agent_text_evidence",
            "value": "Story text",
            "source_agent_message_id": "agent-msg-1",
        }
    ]
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="completed",
            text="Story text",
        )
    ]
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="generate image from story",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-2",
                task="Generate an image based on the story.",
                context_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.CONTEXT,
                        ref_id="story_text",
                        source_agent_message_id="agent-msg-1",
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

    normalized = normalize_context_refs_with_available_facts(action, state)

    assert normalized.targets[0].context_refs[0].ref_id == "agent-msg-1:text_evidence"


def test_rejected_delegate_uses_private_question_text_for_input_required():
    from models.orchestration import AgentInputObservationRecord

    state = _state()
    state.blockers = [
        BlockerRecord(
            key="agent_blocker:agent-1:agent_input_required",
            description="Agent requested additional input.",
            source="agent",
            evidence_refs=["agent-msg-1", "agent-msg-1:awaiting_input"],
            validation_status="validated",
            claimed_user_only=True,
        )
    ]
    state.private_agent_input_observations = [
        AgentInputObservationRecord(
            classification="untyped",
            raw_prompt="Which airport should we fly from?",
            observed_state="input-required",
            authoritative_task_id="task-1",
            authoritative_context_id="ctx-1",
            agent_id="agent-1",
            agent_message_id="agent-msg-1",
        )
    ]

    action = action_for_rejected_delegate(
        state,
        error_code="delegate_blocked_pending_user",
    )

    assert action is not None
    assert action.action == PlannerActionType.ASK_USER
    assert action.questions[0].prompt == "Which airport should we fly from?"
    assert action.questions[0].blocker_keys == [
        "agent_blocker:agent-1:agent_input_required"
    ]
