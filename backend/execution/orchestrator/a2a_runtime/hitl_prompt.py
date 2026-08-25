"""Shared HITL UI prompt-type mapping for orchestrator interactions."""

from __future__ import annotations

from common.dto.hitl import HITLAnswerKind, HITLInteractionKind, HITLQuestionSpec


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


__all__ = ["prompt_type_for_question"]
