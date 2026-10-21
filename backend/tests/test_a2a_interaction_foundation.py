import pytest
from pydantic import ValidationError

from common.dto.hitl import A2AInteractionSpec
from common.types import (
    Message,
    MessageRole,
    Part,
    TextPart,
)


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
