"""Shared HITL UI prompt-type mapping for orchestrator interactions."""

from __future__ import annotations

from common.dto.hitl import (
    HITLAnswer,
    HITLAnswerKind,
    HITLAuthorizationResultAnswer,
    HITLChoiceOption,
    HITLConfirmationAnswer,
    HITLInteractionKind,
    HITLMultiChoiceAnswer,
    HITLPolicyDecision,
    HITLPolicyDecisionAnswer,
    HITLQuestionSpec,
    HITLSingleChoiceAnswer,
    HITLTextAnswer,
)
from execution.orchestrator.public_text import sanitize_public_text


def prompt_type_for_question(question: HITLQuestionSpec) -> str:
    if question.interaction_kind == HITLInteractionKind.AUTH_CHALLENGE:
        return "authentication"
    if question.interaction_kind == HITLInteractionKind.POLICY_DECISION:
        return "approval"
    return {
        HITLAnswerKind.SINGLE_CHOICE: "single_choice",
        HITLAnswerKind.MULTI_CHOICE: "multi_choice",
        HITLAnswerKind.CONFIRMATION: "confirmation",
    }.get(question.answer_kind, "text")


def public_question_fields(
    question: HITLQuestionSpec,
    *,
    secret_values: tuple[str, ...] | None,
) -> dict[str, object]:
    """Header, optional flag, Other, and described choices for the owner's UI.

    ``secret_values=None`` keeps agent text as sent, matching the legacy
    non-canonical frames; otherwise agent text is sanitized and bounded like
    the prompt and choices.
    """

    def public(value: str | None, limit: int) -> str | None:
        if value is None or secret_values is None:
            return value
        return sanitize_public_text(value, secret_values=secret_values)[:limit]

    options = question.choice_options
    if options is not None and secret_values is not None:
        options = [
            HITLChoiceOption(
                value=public(option.value, 500),
                label=public(option.label, 200),
                description=public(option.description, 500),
            )
            for option in options[:20]
        ]
    return {
        "header": public(question.header, 40),
        "required": question.required,
        "allow_other": question.allow_other,
        "choice_options": options,
    }


def public_answer_text(
    question: HITLQuestionSpec,
    answer: HITLAnswer,
    *,
    secret_values: tuple[str, ...],
) -> str:
    """The owner's answer as the owner would read it on the session record.

    Choices show their labels, decisions read Approved or Rejected, and sign-in
    results never expose the authorization reference.
    """

    labels = {
        option.value: option.label or option.value
        for option in question.choice_options or ()
    }
    return sanitize_public_text(
        _answer_text(answer, labels), secret_values=secret_values
    )[:1_000]


def model_answer_text(answer: HITLAnswer) -> str:
    """The owner's answer for the private model transcript.

    Choices keep their values, which the model saw in the question inventory;
    sign-in results never expose the authorization reference.
    """

    return _answer_text(answer, {})[:1_000]


def _answer_text(answer: HITLAnswer, labels: dict[str, str]) -> str:
    if isinstance(answer, HITLTextAnswer):
        text = answer.text
    elif isinstance(answer, HITLSingleChoiceAnswer):
        text = labels.get(answer.choice, answer.choice) if answer.choice else ""
        text = text or answer.other or ""
    elif isinstance(answer, HITLMultiChoiceAnswer):
        parts = [labels.get(choice, choice) for choice in answer.choices]
        text = ", ".join([*parts, *([answer.other] if answer.other else [])])
    elif isinstance(answer, HITLConfirmationAnswer):
        text = "Yes" if answer.confirmed else "No"
    elif isinstance(answer, HITLPolicyDecisionAnswer):
        text = (
            "Approved" if answer.decision == HITLPolicyDecision.APPROVE else "Rejected"
        )
    elif isinstance(answer, HITLAuthorizationResultAnswer):
        text = "Signed in"
    else:
        text = ""
    return text


__all__ = [
    "model_answer_text",
    "prompt_type_for_question",
    "public_answer_text",
    "public_question_fields",
]
