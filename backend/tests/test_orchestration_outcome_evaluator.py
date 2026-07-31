from execution.orchestration.outcome_evaluator import (
    DelegationOutcomeEvaluator,
    canonical_content_fingerprint,
    effective_output_key,
    goal_fingerprints,
    invalidate_required_evidence,
    semantic_fact_map,
)
from models.orchestration import (
    AgentOutputRecord,
    BlockerRecord,
    DispatchExpectedOutput,
    DispatchIntent,
    OrchestrationRunState,
    UnknownRecord,
)


def _state(*, facts=None, artifacts=None, outcomes=None, blockers=None, failures=None):
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="produce a quote artifact",
        candidate_agent_ids=["agent-1"],
        facts=list(facts or []),
        artifacts=list(artifacts or []),
        delegation_outcomes=list(outcomes or []),
        blockers=list(blockers or []),
        open_failures=list(failures or []),
    )


def _intent(message_id, intent_id="intent-1"):
    return DispatchIntent(
        step_id="step-1",
        step_target_id=f"{intent_id}:target",
        dispatch_intent_id=intent_id,
        planned_agent_message_id=message_id,
        agent_id="agent-1",
        task="Produce the quote artifact",
        task_hash="task-hash",
        expected_outputs=[
            DispatchExpectedOutput(
                output_key="quote",
                kind="artifact",
                artifact_name="quote",
                required=True,
                required_fields=[
                    "requested_coverage.limit",
                    "requested_coverage.retention",
                ],
            )
        ],
    )


def _output(message_id, text="quote ready"):
    return AgentOutputRecord(
        agent_message_id=message_id,
        agent_id="agent-1",
        status="completed",
        text=text,
        artifact_keys=[f"{message_id}:artifact:quote"],
    )


def _artifact(message_id, data):
    return {
        "artifact_key": f"{message_id}:artifact:quote",
        "source_agent_message_id": message_id,
        "source_agent_id": "agent-1",
        "name": "quote",
        "parts": [{"data": data}],
    }


def _named_artifact(message_id, name, data):
    artifact = _artifact(message_id, data)
    artifact["name"] = name
    return artifact


def _agent_text(message_id, text):
    return {
        "fact_id": f"{message_id}:text",
        "source_agent_message_id": message_id,
        "source_agent_id": "agent-1",
        "kind": "agent_text",
        "text": text,
    }


def _goal_family(intent):
    return goal_fingerprints(
        agent_id=intent.agent_id,
        expected_outputs=intent.expected_outputs,
        selected_content_fingerprints=[],
        dependency_family_fingerprints=[],
        upstream_output_fingerprints=[],
    ).goal_family_fingerprint


def test_fingerprint_ignores_volatile_projection_fields_and_mapping_order():
    first = {
        "artifact_key": "msg-1:artifact:1",
        "source_agent_message_id": "msg-1",
        "data": {"b": 2, "a": 1},
    }
    second = {
        "source_agent_message_id": "msg-2",
        "artifact_key": "msg-2:artifact:9",
        "data": {"a": 1, "b": 2},
    }

    assert canonical_content_fingerprint(first) == canonical_content_fingerprint(second)


def test_agent_text_fact_is_not_semantic_progress():
    facts = [
        {
            "fact_id": "msg-1:text",
            "kind": "agent_text",
            "text": "same answer",
        }
    ]

    assert semantic_fact_map(facts) == {}


def test_structured_fact_uses_semantic_key_and_canonical_value():
    facts = [
        {
            "fact_id": "volatile-id",
            "kind": "structured",
            "semantic_key": "client.employee_count",
            "value": 250,
        }
    ]

    assert semantic_fact_map(facts) == {"client.employee_count": 250}


def test_effective_output_key_uses_model_normalized_identity():
    output = DispatchExpectedOutput(
        kind="artifact",
        artifact_name="quote",
        required_fields=["pricing.premium"],
    )

    assert effective_output_key(output) == output.output_key


def test_goal_fingerprints_separate_family_evidence_revision_and_agent_attempt():
    outputs = [
        DispatchExpectedOutput(
            kind="artifact",
            artifact_name="quote",
            required_fields=["pricing.premium"],
        )
    ]
    first = goal_fingerprints(
        agent_id="agent-1",
        expected_outputs=outputs,
        selected_content_fingerprints=["resource-1"],
        dependency_family_fingerprints=["dependency-1"],
        upstream_output_fingerprints=[],
    )
    new_evidence = goal_fingerprints(
        agent_id="agent-1",
        expected_outputs=outputs,
        selected_content_fingerprints=["resource-2"],
        dependency_family_fingerprints=["dependency-1"],
        upstream_output_fingerprints=[],
    )
    new_agent = goal_fingerprints(
        agent_id="agent-2",
        expected_outputs=outputs,
        selected_content_fingerprints=["resource-1"],
        dependency_family_fingerprints=["dependency-1"],
        upstream_output_fingerprints=[],
    )

    assert first.goal_family_fingerprint == new_evidence.goal_family_fingerprint
    assert first.evidence_fingerprint != new_evidence.evidence_fingerprint
    assert first.goal_revision_fingerprint != new_evidence.goal_revision_fingerprint
    assert first.goal_revision_fingerprint == new_agent.goal_revision_fingerprint
    assert first.attempt_fingerprint != new_agent.attempt_fingerprint


def test_goal_family_is_stable_when_expected_outputs_are_reordered():
    quote = DispatchExpectedOutput(
        output_key="quote",
        kind="artifact",
        artifact_name="quote",
        required_fields=["pricing.premium"],
    )
    summary = DispatchExpectedOutput(
        output_key="summary",
        kind="summary",
        description="Summarize the quote.",
    )
    first = goal_fingerprints(
        agent_id="agent-1",
        expected_outputs=[quote, summary],
        selected_content_fingerprints=["resource-1"],
        dependency_family_fingerprints=["dependency-1"],
        upstream_output_fingerprints=[],
    )
    reordered = goal_fingerprints(
        agent_id="agent-1",
        expected_outputs=[summary, quote],
        selected_content_fingerprints=["resource-1"],
        dependency_family_fingerprints=["dependency-1"],
        upstream_output_fingerprints=[],
    )

    assert first == reordered


def test_identical_artifact_new_key_and_new_agent_text_is_no_progress():
    evaluator = DelegationOutcomeEvaluator()
    first_after = _state(
        artifacts=[
            _artifact(
                "agent-msg-1",
                {"requested_coverage": {"limit": 1_000_000, "retention": None}},
            )
        ],
        facts=[_agent_text("agent-msg-1", "quote ready")],
    )
    first = evaluator.evaluate(
        _state(), first_after, _intent("agent-msg-1"), _output("agent-msg-1"), {}
    )
    first_after.delegation_outcomes.append(first)
    duplicate_after = first_after.model_copy(deep=True)
    duplicate_after.artifacts.append(
        _artifact(
            "agent-msg-2",
            {"requested_coverage": {"limit": 1_000_000, "retention": None}},
        )
    )
    duplicate_after.facts.append(
        _agent_text("agent-msg-2", "the quote has been prepared")
    )

    second = evaluator.evaluate(
        first_after,
        duplicate_after,
        _intent("agent-msg-2", "intent-2"),
        _output("agent-msg-2", "the quote has been prepared"),
        {},
    )

    assert first.status == "partial"
    assert second.status == "no_progress"
    assert second.changed_fact_keys == []


def test_required_field_reduction_is_partial_progress():
    evaluator = DelegationOutcomeEvaluator()
    after = _state(
        artifacts=[
            _artifact(
                "agent-msg-1",
                {"requested_coverage": {"limit": 1_000_000, "retention": None}},
            )
        ]
    )

    outcome = evaluator.evaluate(
        _state(), after, _intent("agent-msg-1"), _output("agent-msg-1"), {}
    )

    assert outcome.status == "partial"
    assert outcome.newly_satisfied_required_obligations == [
        "quote:$present",
        "quote:requested_coverage.limit",
    ]
    assert outcome.remaining_required_obligations == [
        "quote:requested_coverage.retention"
    ]


def test_required_obligation_cannot_regress_without_invalidation_event():
    evaluator = DelegationOutcomeEvaluator()
    retained = _artifact(
        "agent-msg-1",
        {"requested_coverage": {"limit": 1_000_000, "retention": 25_000}},
    )
    before = _state(artifacts=[retained])
    prior = evaluator.evaluate(
        _state(), before, _intent("agent-msg-1"), _output("agent-msg-1"), {}
    )
    before.delegation_outcomes.append(prior)
    after = before.model_copy(deep=True)
    after.facts.append(_agent_text("agent-msg-2", "no additional fields"))

    outcome = evaluator.evaluate(
        before,
        after,
        _intent("agent-msg-2", "intent-2"),
        _output("agent-msg-2", "no additional fields"),
        {},
    )

    assert (
        "quote:requested_coverage.limit" not in outcome.remaining_required_obligations
    )


def test_invalidated_required_evidence_is_not_restored_from_retained_artifact():
    evaluator = DelegationOutcomeEvaluator()
    intent = _intent("agent-msg-2")
    after, _ = invalidate_required_evidence(
        _state(
            artifacts=[
                _artifact(
                    "agent-msg-1",
                    {
                        "requested_coverage": {
                            "limit": 1_000_000,
                            "retention": 25_000,
                        }
                    },
                )
            ]
        ),
        goal_family_fingerprint=_goal_family(intent),
        evidence_key="quote-evidence",
        obligation_keys=[
            "quote:$present",
            "quote:requested_coverage.limit",
            "quote:requested_coverage.retention",
        ],
        reason="The retained quote evidence is no longer valid.",
        source_event_id="event-1",
    )

    output = _output("agent-msg-2")
    output.artifact_keys = []
    outcome = evaluator.evaluate(_state(), after, intent, output, {})

    assert outcome.status == "no_progress"
    assert outcome.remaining_required_obligations == [
        "quote:$present",
        "quote:requested_coverage.limit",
        "quote:requested_coverage.retention",
    ]


def test_fresh_delegation_evidence_supersedes_prior_invalidation():
    evaluator = DelegationOutcomeEvaluator()
    intent = _intent("agent-msg-2")
    after, _ = invalidate_required_evidence(
        _state(
            artifacts=[
                _artifact(
                    "agent-msg-2",
                    {
                        "requested_coverage": {
                            "limit": 2_000_000,
                            "retention": None,
                        }
                    },
                )
            ]
        ),
        goal_family_fingerprint=_goal_family(intent),
        evidence_key="stale-quote-evidence",
        obligation_keys=[
            "quote:$present",
            "quote:requested_coverage.limit",
            "quote:requested_coverage.retention",
        ],
        reason="An earlier quote was invalidated.",
        source_event_id="event-1",
    )

    outcome = evaluator.evaluate(_state(), after, intent, _output("agent-msg-2"), {})

    assert outcome.status == "partial"
    assert outcome.newly_satisfied_required_obligations == [
        "quote:$present",
        "quote:requested_coverage.limit",
    ]
    assert outcome.remaining_required_obligations == [
        "quote:requested_coverage.retention"
    ]


def test_unrelated_artifact_does_not_satisfy_delegation_obligations():
    evaluator = DelegationOutcomeEvaluator()
    output = _output("agent-msg-1")
    output.artifact_keys = []
    after = _state(
        artifacts=[
            _artifact(
                "unrelated-msg",
                {"requested_coverage": {"limit": 1_000_000, "retention": 25_000}},
            )
        ]
    )

    outcome = evaluator.evaluate(_state(), after, _intent("agent-msg-1"), output, {})

    assert outcome.status == "no_progress"
    assert outcome.satisfied_output_keys == []


def test_invalidation_is_scoped_to_its_goal_family():
    evaluator = DelegationOutcomeEvaluator()
    other_intent = _intent("agent-msg-1")
    other_intent.expected_outputs[0].required_fields = ["pricing.premium"]
    first_after = _state(
        artifacts=[_artifact("agent-msg-1", {"pricing": {"premium": 10_000}})]
    )
    first = evaluator.evaluate(
        _state(), first_after, other_intent, _output("agent-msg-1"), {}
    )
    before = _state(outcomes=[first])
    regular_intent = _intent("agent-msg-2")
    after, _ = invalidate_required_evidence(
        before,
        goal_family_fingerprint=_goal_family(regular_intent),
        evidence_key="quote-evidence",
        obligation_keys=["quote:$present", "quote:pricing.premium"],
        reason="A different quote family was invalidated.",
        source_event_id="event-1",
    )
    repeated_intent = other_intent.model_copy(deep=True)
    repeated_intent.dispatch_intent_id = "intent-2"
    repeated_intent.planned_agent_message_id = "agent-msg-2"
    repeated_output = _output("agent-msg-2")
    repeated_output.artifact_keys = []

    outcome = evaluator.evaluate(before, after, repeated_intent, repeated_output, {})

    assert outcome.status == "fulfilled"
    assert outcome.remaining_required_obligations == []


def test_fresh_required_evidence_after_invalidation_is_retained_later():
    evaluator = DelegationOutcomeEvaluator()
    intent = _intent("agent-msg-2", "intent-2")
    invalidated, _ = invalidate_required_evidence(
        _state(),
        goal_family_fingerprint=_goal_family(intent),
        evidence_key="quote-evidence",
        obligation_keys=[
            "quote:$present",
            "quote:requested_coverage.limit",
            "quote:requested_coverage.retention",
        ],
        reason="The retained quote evidence is no longer valid.",
        source_event_id="event-1",
    )
    fresh_after = invalidated.model_copy(deep=True)
    fresh_after.artifacts.append(
        _artifact(
            "agent-msg-2",
            {"requested_coverage": {"limit": 1_000_000, "retention": 25_000}},
        )
    )

    fresh = evaluator.evaluate(
        invalidated,
        fresh_after,
        intent,
        _output("agent-msg-2"),
        {},
    )
    later_before = fresh_after.model_copy(deep=True)
    later_before.delegation_outcomes.append(fresh)
    later_after = later_before.model_copy(deep=True)
    later_after.facts.append(_agent_text("agent-msg-3", "no additional evidence"))

    later = evaluator.evaluate(
        later_before,
        later_after,
        _intent("agent-msg-3", "intent-3"),
        _output("agent-msg-3", "no additional evidence"),
        {},
    )

    assert fresh.status == "fulfilled"
    assert later.status == "fulfilled"
    assert later.remaining_required_obligations == []


def test_required_output_presence_is_partial_progress_when_fields_are_missing():
    evaluator = DelegationOutcomeEvaluator()
    after = _state(artifacts=[_artifact("agent-msg-1", {})])

    outcome = evaluator.evaluate(
        _state(), after, _intent("agent-msg-1"), _output("agent-msg-1"), {}
    )

    assert outcome.status == "partial"
    assert outcome.newly_satisfied_required_obligations == ["quote:$present"]
    assert outcome.remaining_required_obligations == [
        "quote:requested_coverage.limit",
        "quote:requested_coverage.retention",
    ]


def test_output_owned_artifact_presence_counts_when_artifact_name_differs():
    evaluator = DelegationOutcomeEvaluator()
    after = _state(
        artifacts=[
            _named_artifact(
                "agent-msg-1",
                "cyber_submission",
                {"company": {"name": "Acme"}},
            )
        ]
    )

    outcome = evaluator.evaluate(
        _state(), after, _intent("agent-msg-1"), _output("agent-msg-1"), {}
    )

    assert outcome.status == "partial"
    assert outcome.newly_satisfied_required_obligations == ["quote:$present"]
    assert outcome.remaining_required_obligations == [
        "quote:requested_coverage.limit",
        "quote:requested_coverage.retention",
    ]


def test_one_named_artifact_does_not_satisfy_multiple_named_outputs():
    evaluator = DelegationOutcomeEvaluator()
    intent = _intent("agent-msg-1")
    intent.expected_outputs = [
        DispatchExpectedOutput(
            output_key="quote",
            kind="artifact",
            artifact_name="quote",
            required=True,
        ),
        DispatchExpectedOutput(
            output_key="report",
            kind="artifact",
            artifact_name="report",
            required=True,
        ),
    ]
    after = _state(artifacts=[_artifact("agent-msg-1", {"quote": "ready"})])

    outcome = evaluator.evaluate(_state(), after, intent, _output("agent-msg-1"), {})

    assert outcome.status == "partial"
    assert outcome.newly_satisfied_required_obligations == ["quote:$present"]
    assert outcome.remaining_required_obligations == ["report:$present"]


def test_required_output_presence_is_retained_from_prior_outcome():
    evaluator = DelegationOutcomeEvaluator()
    intent = _intent("agent-msg-1")
    intent.expected_outputs[0].required_fields = []
    first_after = _state(artifacts=[_artifact("agent-msg-1", {"quote": "ready"})])
    first = evaluator.evaluate(
        _state(), first_after, intent, _output("agent-msg-1"), {}
    )
    before = _state(outcomes=[first])
    repeated_intent = _intent("agent-msg-2", "intent-2")
    repeated_intent.expected_outputs[0].required_fields = []

    outcome = evaluator.evaluate(
        before,
        _state(outcomes=[first]),
        repeated_intent,
        _output("agent-msg-2"),
        {},
    )

    assert outcome.status == "fulfilled"


def test_all_required_obligations_satisfied_returns_fulfilled():
    evaluator = DelegationOutcomeEvaluator()
    after = _state(
        artifacts=[
            _artifact(
                "agent-msg-1",
                {"requested_coverage": {"limit": 1_000_000, "retention": 25_000}},
            )
        ]
    )

    outcome = evaluator.evaluate(
        _state(), after, _intent("agent-msg-1"), _output("agent-msg-1"), {}
    )

    assert outcome.status == "fulfilled"


def test_validated_user_only_blocker_returns_blocked():
    evaluator = DelegationOutcomeEvaluator()
    after = _state(
        blockers=[
            {
                "key": "missing-user-input",
                "description": "A user must provide the required information.",
                "blocked_output_keys": ["quote"],
                "source": "agent",
                "claimed_user_only": True,
                "validation_status": "validated",
            }
        ]
    )

    outcome = evaluator.evaluate(
        _state(), after, _intent("agent-msg-1"), _output("agent-msg-1"), {}
    )

    assert outcome.status == "blocked"


def test_outcome_carries_relevant_unknowns_and_validated_blockers():
    evaluator = DelegationOutcomeEvaluator()
    blocker = BlockerRecord(
        key="blocker-1",
        description="Need requested limit.",
        blocked_output_keys=["quote"],
        source="agent",
        evidence_refs=["agent-msg-1:artifact:quote"],
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
    )
    unknown = UnknownRecord(
        key="unknown-1",
        description="requested limit is missing",
        source_agent_message_id="agent-msg-1",
        applies_to_output_keys=["quote"],
    )
    after = _state(
        artifacts=[_artifact("agent-msg-1", {"requested_coverage": {"limit": None}})],
        blockers=[blocker],
    )
    after.unknowns = [unknown]

    outcome = evaluator.evaluate(
        _state(),
        after,
        _intent("agent-msg-1"),
        _output("agent-msg-1"),
        {},
    )

    assert outcome.status == "blocked"
    assert outcome.unknowns == [unknown]
    assert outcome.blockers == [blocker]


def test_transport_failure_returns_failed():
    evaluator = DelegationOutcomeEvaluator()
    after = _state(
        failures=[
            {
                "failure_id": "failure-1",
                "fingerprint": "failure-fingerprint",
                "source": "a2a_adapter",
                "agent_id": "agent-1",
                "agent_message_id": "agent-msg-1",
                "dispatch_intent_id": "intent-1",
                "error_code": "transport_error",
                "error_message": "Agent transport failed.",
                "recoverable": True,
            }
        ]
    )

    outcome = evaluator.evaluate(
        _state(), after, _intent("agent-msg-1"), _output("agent-msg-1"), {}
    )

    assert outcome.status == "failed"


def test_optional_only_change_after_partial_result_returns_no_progress():
    evaluator = DelegationOutcomeEvaluator()
    partial_after = _state(
        artifacts=[
            _artifact(
                "agent-msg-1",
                {"requested_coverage": {"limit": 1_000_000, "retention": None}},
            )
        ]
    )
    partial = evaluator.evaluate(
        _state(), partial_after, _intent("agent-msg-1"), _output("agent-msg-1"), {}
    )
    partial_after.delegation_outcomes.append(partial)
    after = partial_after.model_copy(deep=True)
    after.artifacts.append(_artifact("agent-msg-2", {"optional": "new value"}))

    outcome = evaluator.evaluate(
        partial_after,
        after,
        _intent("agent-msg-2", "intent-2"),
        _output("agent-msg-2"),
        {},
    )

    assert outcome.status == "no_progress"


def test_first_legacy_text_only_completed_result_returns_fulfilled():
    evaluator = DelegationOutcomeEvaluator()
    intent = _intent("agent-msg-1")
    intent.expected_outputs = []

    outcome = evaluator.evaluate(
        _state(), _state(), intent, _output("agent-msg-1", "prior result"), {}
    )

    assert outcome.status == "fulfilled"


def test_optional_only_outputs_without_evidence_return_no_progress():
    evaluator = DelegationOutcomeEvaluator()
    intent = _intent("agent-msg-1")
    intent.expected_outputs[0].required = False
    output = _output("agent-msg-1")
    output.artifact_keys = []

    outcome = evaluator.evaluate(_state(), _state(), intent, output, {})

    assert outcome.status == "no_progress"


def test_optional_only_outputs_with_matching_evidence_return_fulfilled():
    evaluator = DelegationOutcomeEvaluator()
    intent = _intent("agent-msg-1")
    intent.expected_outputs[0].required = False
    after = _state(
        artifacts=[
            _artifact(
                "agent-msg-1",
                {"requested_coverage": {"limit": 1_000_000}},
            )
        ]
    )

    outcome = evaluator.evaluate(_state(), after, intent, _output("agent-msg-1"), {})

    assert outcome.status == "fulfilled"


def test_empty_legacy_completed_result_is_not_fulfilled():
    evaluator = DelegationOutcomeEvaluator()
    intent = _intent("agent-msg-1")
    intent.expected_outputs = []
    output = _output("agent-msg-1", "")
    output.artifact_keys = []

    outcome = evaluator.evaluate(_state(), _state(), intent, output, {})

    assert outcome.status == "no_progress"


def test_input_required_result_is_blocked_even_without_required_outputs():
    evaluator = DelegationOutcomeEvaluator()
    intent = _intent("agent-msg-1")
    intent.expected_outputs = []
    output = AgentOutputRecord(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="awaiting_input",
        status_message="Need the complete broker submission.",
        interactive_state="input-required",
    )

    outcome = evaluator.evaluate(_state(), _state(), intent, output, {})

    assert outcome.status == "blocked"


def test_repeated_legacy_text_with_same_normalized_fingerprint_returns_no_progress():
    evaluator = DelegationOutcomeEvaluator()
    intent = _intent("agent-msg-1")
    intent.expected_outputs = []
    first_after = _state()
    first = evaluator.evaluate(
        _state(), first_after, intent, _output("agent-msg-1", "prior result"), {}
    )
    first_after.delegation_outcomes.append(first)
    duplicate_intent = _intent("agent-msg-2", "intent-2")
    duplicate_intent.expected_outputs = []

    outcome = evaluator.evaluate(
        first_after,
        first_after.model_copy(deep=True),
        duplicate_intent,
        _output("agent-msg-2", "  prior   result  "),
        {},
    )

    assert outcome.status == "no_progress"


def test_invalidate_required_evidence_records_coded_payload():
    state = _state()

    updated, payload = invalidate_required_evidence(
        state,
        goal_family_fingerprint="goal-family-1",
        evidence_key="quote-evidence",
        obligation_keys=["quote:requested_coverage.limit", "quote:$present"],
        reason="The evidence is no longer valid.",
        source_event_id="event-1",
    )

    assert updated is not state
    assert payload == updated.decision_log[-1]
    assert payload["code"] == "required_evidence_invalidated"
    assert payload["goal_family_fingerprint"] == "goal-family-1"
