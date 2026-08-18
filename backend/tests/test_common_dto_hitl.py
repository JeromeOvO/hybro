from dataclasses import asdict
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from common.dto.delivery import TaskUpdateEvent
from common.dto.hitl import (
    HITLAnswer,
    HITLAnswerKind,
    HITLApplicationRoute,
    HITLAuthorizationResultAnswer,
    HITLCancelCommand,
    HITLInteractionKind,
    HITLPolicyDecisionAnswer,
    HITLPublicSource,
    HITLQuestionAnswer,
    HITLQuestionSpec,
    HITLRouteSnapshot,
)
from delivery.translator import to_sse_frame
from execution.dispatch.agent_event import AgentEvent, AgentInputObservation


def test_hitl_contract_enum_values_are_stable():
    assert {item.value for item in HITLInteractionKind} == {
        "questionnaire",
        "auth_challenge",
        "policy_decision",
    }
    assert {item.value for item in HITLApplicationRoute} == {
        "supervisor_run",
        "a2a_resume",
    }
    assert {item.value for item in HITLPublicSource} == {
        "supervisor",
        "agent",
        "system",
    }
    assert {item.value for item in HITLAnswerKind} == {
        "text",
        "single_choice",
        "multi_choice",
        "confirmation",
        "authorization_result",
        "policy_decision",
    }
    assert "cancel" not in {item.value for item in HITLAnswerKind}


def test_hitl_answer_union_is_discriminated_and_strict():
    adapter = TypeAdapter(HITLAnswer)

    answer = adapter.validate_python({"kind": "single_choice", "choice": "option-a"})
    assert answer.kind == HITLAnswerKind.SINGLE_CHOICE

    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "cancel", "reason": "changed mind"})
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"kind": "confirmation", "confirmed": True, "unexpected": "field"}
        )


def test_auth_contract_accepts_only_an_opaque_reference_and_rejects_secrets():
    answer = HITLAuthorizationResultAnswer(
        authorization_reference="authref:opaque-result-id"
    )
    assert answer.authorization_reference == "authref:opaque-result-id"
    assert "secret" not in HITLAuthorizationResultAnswer.model_fields
    assert "text" not in HITLAuthorizationResultAnswer.model_fields

    with pytest.raises(ValidationError):
        HITLAuthorizationResultAnswer(
            authorization_reference="authref:result-1",
            secret="plaintext-secret",
        )
    with pytest.raises(ValidationError):
        HITLAuthorizationResultAnswer(
            authorization_reference="plaintext-password-or-token",
        )
    with pytest.raises(ValidationError):
        HITLQuestionSpec(
            question_id="auth-1",
            interaction_kind="auth_challenge",
            prompt="Authorize access",
            answer_kind="text",
        )


def test_security_sensitive_scalars_are_strict():
    adapter = TypeAdapter(HITLAnswer)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "confirmation", "confirmed": "yes"})
    with pytest.raises(ValidationError):
        HITLCancelCommand(
            interaction_id="interaction-1",
            expected_interaction_version="1",
            client_request_id="request-1",
        )


def test_policy_answer_requires_approve_or_deny_and_bounds_optional_reason():
    answer = HITLPolicyDecisionAnswer(decision="deny", reason="Insufficient scope")
    assert answer.decision.value == "deny"

    with pytest.raises(ValidationError):
        HITLPolicyDecisionAnswer(decision="abstain")
    with pytest.raises(ValidationError):
        HITLPolicyDecisionAnswer(decision="approve", reason="x" * 1_001)


def test_question_choice_inventory_and_answer_selection_are_validated():
    with pytest.raises(ValidationError):
        HITLQuestionSpec(
            question_id="q-1",
            interaction_kind="questionnaire",
            prompt="Choose one",
            answer_kind="single_choice",
        )
    with pytest.raises(ValidationError):
        HITLQuestionSpec(
            question_id="q-1",
            interaction_kind="questionnaire",
            prompt="Choose one",
            answer_kind="single_choice",
            choices=["a", "a"],
        )

    question = HITLQuestionSpec(
        question_id="q-1",
        interaction_kind="questionnaire",
        prompt="Choose one",
        answer_kind="single_choice",
        choices=["a", "b"],
    )
    accepted = HITLQuestionAnswer(
        question_id="q-1",
        answer={"kind": "single_choice", "choice": "a"},
    )
    question.validate_answer(accepted)

    rejected = HITLQuestionAnswer(
        question_id="q-1",
        answer={"kind": "single_choice", "choice": "not-in-inventory"},
    )
    with pytest.raises(ValueError, match="inventory"):
        question.validate_answer(rejected)


def test_route_snapshot_requires_route_specific_identifiers():
    with pytest.raises(ValidationError, match="orchestration_run_id"):
        HITLRouteSnapshot(route="supervisor_run")
    with pytest.raises(ValidationError, match="context_id"):
        HITLRouteSnapshot(
            route="a2a_resume",
            task_id="task-1",
            continuation_message_id="message-1",
            agent_id="agent-1",
        )

    with pytest.raises(ValidationError, match="must not include an A2A target"):
        HITLRouteSnapshot(
            route="supervisor_run",
            orchestration_run_id="run-1",
            task_id="task-1",
        )
    with pytest.raises(ValidationError, match="must not include orchestration_run_id"):
        HITLRouteSnapshot(
            route="a2a_resume",
            orchestration_run_id="run-1",
            task_id="task-1",
            context_id="context-1",
            continuation_message_id="message-1",
            agent_id="agent-1",
        )

    snapshot = HITLRouteSnapshot(
        route="supervisor_run",
        orchestration_run_id="run-1",
    )
    reconstructed = HITLRouteSnapshot.model_validate(
        {"orchestration_run_id": "run-1", "route": "supervisor_run"}
    )
    assert snapshot.fingerprint == reconstructed.fingerprint
    assert (
        snapshot.fingerprint
        == "b4952cb648d9a380f0ecaddf4d73a5ab3dae7c3da7853f66f8954685bf2837b2"
    )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("task_id", "pending-task-1"),
        ("context_id", "relay-pending-context-1"),
        ("continuation_message_id", "provisional-message-1"),
        ("agent_id", "unknown"),
    ],
)
def test_route_snapshot_rejects_provisional_a2a_identifiers(
    field_name: str,
    field_value: str,
):
    values = {
        "route": "a2a_resume",
        "task_id": "task-1",
        "context_id": "context-1",
        "continuation_message_id": "message-1",
        "agent_id": "agent-1",
        field_name: field_value,
    }
    with pytest.raises(ValidationError, match="authoritative"):
        HITLRouteSnapshot.model_validate(values)


def test_cancel_is_a_versioned_command_not_an_answer():
    with pytest.raises(ValidationError):
        HITLCancelCommand(
            interaction_id="interaction-1",
            expected_interaction_version=0,
            client_request_id="request-1",
        )

    command = HITLCancelCommand(
        interaction_id="interaction-1",
        expected_interaction_version=1,
        client_request_id="request-1",
        reason="No longer needed",
    )
    assert command.expected_interaction_version == 1


def test_private_agent_observation_is_absent_from_public_delivery_translation():
    observation = AgentInputObservation(
        raw_prompt="private raw prompt",
        interaction_metadata={"auth_hint": "private metadata"},
        task_id="task-1",
        context_id="context-1",
        observed_state="auth-required",
    )
    event = AgentEvent(
        kind="interactive",
        message_id="message-1",
        room_id="room-1",
        agent_id="agent-1",
        input_observation=observation,
    )
    assert event.private_input_observation is observation
    assert "input_observation" not in asdict(event)
    assert "private raw prompt" not in str(asdict(event))
    assert "_input_observation" not in TaskUpdateEvent.model_fields

    public_event = TaskUpdateEvent(
        room_id=event.room_id,
        message_id=event.message_id,
        status="input-required",
        agent_id=event.agent_id,
    )
    frame = to_sse_frame(public_event, timestamp=datetime(2025, 1, 1, tzinfo=UTC))
    assert "_input_observation" not in frame["data"]
    assert "private raw prompt" not in str(frame)
    assert "private metadata" not in str(frame)
