from execution.orchestration.outcome_evaluator import (
    canonical_content_fingerprint,
    effective_output_key,
    goal_fingerprints,
    semantic_fact_map,
)
from models.orchestration import DispatchExpectedOutput


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
