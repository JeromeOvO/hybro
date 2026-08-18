from dataclasses import asdict

import pytest
from pydantic import ValidationError

from common.a2a_constants import HYBRO_A2A_INTERACTION_METADATA_KEY
from common.dto.hitl import A2AInteractionSpec
from common.types import (
    Message,
    MessageRole,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from execution.dispatch.a2a_interaction import (
    A2AInteractionDisposition,
    extract_a2a_interaction_spec,
)
from execution.dispatch.agent_event import AgentEvent, AgentInputObservation
from models.orchestration import (
    AgentInputObservationRecord,
    OrchestrationEventType,
    OrchestrationRunEvent,
    OrchestrationRunState,
)
from models.run import Run, RunEvent, RunEventType


def _questions() -> list[dict]:
    return [
        {
            "question_id": "text",
            "interaction_kind": "questionnaire",
            "prompt": "Describe the trip",
            "answer_kind": "text",
        },
        {
            "question_id": "single",
            "interaction_kind": "questionnaire",
            "prompt": "Choose one",
            "answer_kind": "single_choice",
            "choices": ["a", "b"],
        },
        {
            "question_id": "multi",
            "interaction_kind": "questionnaire",
            "prompt": "Choose several",
            "answer_kind": "multi_choice",
            "choices": ["a", "b"],
        },
        {
            "question_id": "confirm",
            "interaction_kind": "questionnaire",
            "prompt": "Confirm",
            "answer_kind": "confirmation",
        },
        {
            "question_id": "auth",
            "interaction_kind": "auth_challenge",
            "prompt": "Authorize",
            "answer_kind": "authorization_result",
        },
        {
            "question_id": "policy",
            "interaction_kind": "policy_decision",
            "prompt": "Approve or deny",
            "answer_kind": "policy_decision",
        },
    ]


def _spec_data() -> dict:
    return {
        "schema_version": 1,
        "interaction_id": "remote-interaction-1",
        "questions": _questions(),
    }


def _message(metadata: dict | None) -> Message:
    return Message(
        role=MessageRole.AGENT,
        messageId="remote-message",
        parts=[Part(root=TextPart(text="Please answer"))],
        metadata=metadata,
    )


def _task(*, message_metadata=None, task_metadata=None) -> Task:
    return Task(
        id="authoritative-task",
        contextId="authoritative-context",
        status=TaskStatus(
            state=TaskState.input_required,
            message=_message(message_metadata),
        ),
        metadata=task_metadata,
    )


def test_a2a_interaction_spec_accepts_all_question_kinds_and_is_strict():
    spec = A2AInteractionSpec.model_validate(_spec_data())
    assert len(spec.questions) == 6

    with pytest.raises(ValidationError, match="schema_version"):
        A2AInteractionSpec.model_validate(
            {
                key: value
                for key, value in _spec_data().items()
                if key != "schema_version"
            }
        )
    for invalid_version in (True, "1", 2):
        with pytest.raises(ValidationError, match="schema_version"):
            A2AInteractionSpec.model_validate(
                {**_spec_data(), "schema_version": invalid_version}
            )
    with pytest.raises(ValidationError):
        A2AInteractionSpec.model_validate({**_spec_data(), "unknown": True})
    with pytest.raises(ValidationError):
        A2AInteractionSpec.model_validate({**_spec_data(), "interaction_id": " "})
    for invalid_required in (0, 1, "true", "false", "yes"):
        questions = _questions()
        questions[0]["required"] = invalid_required
        with pytest.raises(ValidationError, match="required"):
            A2AInteractionSpec.model_validate({**_spec_data(), "questions": questions})


@pytest.mark.parametrize("count", [0, 101])
def test_a2a_interaction_spec_bounds_question_inventory(count: int):
    questions = [
        {
            "question_id": f"q-{index}",
            "interaction_kind": "questionnaire",
            "prompt": "Question",
            "answer_kind": "text",
        }
        for index in range(count)
    ]
    with pytest.raises(ValidationError):
        A2AInteractionSpec.model_validate({**_spec_data(), "questions": questions})


def test_a2a_interaction_spec_rejects_duplicate_question_ids():
    questions = _questions()
    questions[1]["question_id"] = questions[0]["question_id"]
    with pytest.raises(ValidationError, match="unique"):
        A2AInteractionSpec.model_validate({**_spec_data(), "questions": questions})


def test_extractor_accepts_only_status_message_metadata_for_task_and_event():
    metadata = {HYBRO_A2A_INTERACTION_METADATA_KEY: _spec_data()}
    task_result = extract_a2a_interaction_spec(_task(message_metadata=metadata))
    event_result = extract_a2a_interaction_spec(
        TaskStatusUpdateEvent(
            taskId="authoritative-task",
            contextId="authoritative-context",
            status=TaskStatus(
                state=TaskState.input_required,
                message=_message(metadata),
            ),
            metadata={HYBRO_A2A_INTERACTION_METADATA_KEY: {"invalid": "ignored"}},
        )
    )
    assert task_result.disposition == A2AInteractionDisposition.TYPED
    assert event_result.disposition == A2AInteractionDisposition.TYPED
    assert task_result.spec == event_result.spec

    alternate_only = _task(
        task_metadata={HYBRO_A2A_INTERACTION_METADATA_KEY: _spec_data()}
    )
    assert (
        extract_a2a_interaction_spec(alternate_only).disposition
        == A2AInteractionDisposition.UNTYPED
    )
    with pytest.raises(TypeError):
        extract_a2a_interaction_spec({"status": {}})  # type: ignore[arg-type]


def test_extractor_distinguishes_absent_and_invalid_namespace():
    absent = extract_a2a_interaction_spec(_task(message_metadata={"other": True}))
    invalid = extract_a2a_interaction_spec(
        _task(
            message_metadata={
                HYBRO_A2A_INTERACTION_METADATA_KEY: {
                    **_spec_data(),
                    "unexpected": True,
                }
            }
        )
    )
    assert absent.disposition == A2AInteractionDisposition.UNTYPED
    assert absent.validation_error is None
    assert invalid.disposition == A2AInteractionDisposition.INVALID
    assert invalid.spec is None
    assert invalid.validation_error


def test_metadata_identifiers_never_replace_authoritative_transport_identity():
    metadata = {
        "task_id": "spoofed-task",
        "context_id": "spoofed-context",
        HYBRO_A2A_INTERACTION_METADATA_KEY: _spec_data(),
    }
    parsed = extract_a2a_interaction_spec(_task(message_metadata=metadata))
    observation = AgentInputObservation(
        raw_prompt="Please answer",
        interaction_metadata=parsed.raw_metadata,
        interaction_spec=parsed.spec,
        parser_disposition=parsed.disposition,
        task_id="authoritative-task",
        context_id="authoritative-context",
        observed_state="input-required",
    )
    event = AgentEvent(
        kind="interactive",
        message_id="local-agent-message",
        room_id="room-1",
        agent_id="agent-1",
        details={"classification": "typed"},
        input_observation=observation,
    )
    assert event.private_input_observation is observation
    assert observation.task_id == "authoritative-task"
    assert observation.context_id == "authoritative-context"
    assert "input_observation" not in asdict(event)
    metadata[HYBRO_A2A_INTERACTION_METADATA_KEY]["interaction_id"] = "mutated"
    assert (
        observation.interaction_metadata[HYBRO_A2A_INTERACTION_METADATA_KEY][
            "interaction_id"
        ]
        == "remote-interaction-1"
    )
    with pytest.raises(TypeError):
        observation.interaction_metadata[HYBRO_A2A_INTERACTION_METADATA_KEY][
            "interaction_id"
        ] = "mutated-through-observation"


def test_private_durable_observation_round_trips_without_public_run_leak():
    sentinel = "PRIVATE_A2A_PROMPT_SENTINEL"
    spec = A2AInteractionSpec.model_validate(_spec_data())
    record = AgentInputObservationRecord(
        classification="typed",
        raw_prompt=sentinel,
        raw_metadata={HYBRO_A2A_INTERACTION_METADATA_KEY: _spec_data()},
        interaction_spec=spec,
        observed_state="input-required",
        authoritative_task_id="remote-task-1",
        authoritative_context_id="remote-context-1",
        agent_id="agent-1",
        agent_message_id="agent-message-1",
    )
    reconstructed = AgentInputObservationRecord.model_validate(record.model_dump())
    assert reconstructed.observation_id == record.observation_id

    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-message-1",
        goal="Plan a trip",
        candidate_agent_ids=["agent-1"],
        private_agent_input_observations=[record],
    )
    assert sentinel in state.model_dump_json()

    private_event = OrchestrationRunEvent(
        run_id=state.run_id,
        room_id=state.room_id,
        type=OrchestrationEventType.STATE_REDUCED,
        state_version=state.state_version,
        payload={"status": state.status.value},
    )
    public_run = Run(run_id=state.run_id, room_id=state.room_id)
    public_event = RunEvent(
        run_id=state.run_id,
        room_id=state.room_id,
        seq=1,
        type=RunEventType.RUN_STARTED,
    )
    for boundary in (private_event, public_run, public_event):
        serialized = boundary.model_dump_json()
        assert "private_agent_input_observations" not in serialized
        assert sentinel not in serialized
    assert "private_agent_input_observations" not in Run.model_fields
    assert "private_agent_input_observations" not in RunEvent.model_fields


@pytest.mark.parametrize(
    ("task_id", "context_id"),
    [("relay-pending-task", "context-1"), ("task-1", "unknown")],
)
def test_durable_observation_requires_authoritative_identifiers(
    task_id: str, context_id: str
):
    with pytest.raises(ValidationError, match="authoritative"):
        AgentInputObservationRecord(
            classification="untyped",
            raw_prompt="Question",
            observed_state="input-required",
            authoritative_task_id=task_id,
            authoritative_context_id=context_id,
            agent_id="agent-1",
            agent_message_id="message-1",
        )
