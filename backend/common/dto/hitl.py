"""Dependency-neutral contracts for the next HITL lifecycle.

These contracts intentionally have no dependency on persistence, routing, or
application services.  R0 defines them without changing the existing HITL API.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from common.dto.base import FrozenDTO

_NonBlankId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
_Prompt = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000),
]
_ShortReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]
_AuthorizationReference = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^authref:[A-Za-z0-9._-]{1,200}$",
    ),
]
_StrictVersion = Annotated[int, Field(strict=True, ge=1)]


class HITLInteractionKind(StrEnum):
    QUESTIONNAIRE = "questionnaire"
    AUTH_CHALLENGE = "auth_challenge"
    POLICY_DECISION = "policy_decision"


class HITLApplicationRoute(StrEnum):
    SUPERVISOR_RUN = "supervisor_run"
    A2A_RESUME = "a2a_resume"


class HITLPublicSource(StrEnum):
    SUPERVISOR = "supervisor"
    AGENT = "agent"
    SYSTEM = "system"


class HITLEvidenceOrigin(StrEnum):
    """Where the need for human evidence was observed."""

    AGENT = "agent"
    SUPERVISOR = "supervisor"
    SYSTEM = "system"


class HITLAnswerKind(StrEnum):
    TEXT = "text"
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    CONFIRMATION = "confirmation"
    AUTHORIZATION_RESULT = "authorization_result"
    POLICY_DECISION = "policy_decision"


class HITLPolicyDecision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"


class _HITLContract(FrozenDTO):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HITLTextAnswer(_HITLContract):
    kind: Literal[HITLAnswerKind.TEXT] = HITLAnswerKind.TEXT
    text: _Prompt


class HITLSingleChoiceAnswer(_HITLContract):
    kind: Literal[HITLAnswerKind.SINGLE_CHOICE] = HITLAnswerKind.SINGLE_CHOICE
    choice: _NonBlankId


class HITLMultiChoiceAnswer(_HITLContract):
    kind: Literal[HITLAnswerKind.MULTI_CHOICE] = HITLAnswerKind.MULTI_CHOICE
    choices: list[_NonBlankId] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_choices(self) -> Self:
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("choices must be unique")
        return self


class HITLConfirmationAnswer(_HITLContract):
    kind: Literal[HITLAnswerKind.CONFIRMATION] = HITLAnswerKind.CONFIRMATION
    confirmed: StrictBool


class HITLAuthorizationResultAnswer(_HITLContract):
    """Reference issued and resolved by a trusted auth adapter, never user text."""

    kind: Literal[HITLAnswerKind.AUTHORIZATION_RESULT] = (
        HITLAnswerKind.AUTHORIZATION_RESULT
    )
    authorization_reference: _AuthorizationReference


class HITLPolicyDecisionAnswer(_HITLContract):
    kind: Literal[HITLAnswerKind.POLICY_DECISION] = HITLAnswerKind.POLICY_DECISION
    decision: HITLPolicyDecision
    reason: _ShortReason | None = None


HITLAnswer = Annotated[
    HITLTextAnswer
    | HITLSingleChoiceAnswer
    | HITLMultiChoiceAnswer
    | HITLConfirmationAnswer
    | HITLAuthorizationResultAnswer
    | HITLPolicyDecisionAnswer,
    Field(discriminator="kind"),
]


class HITLQuestionSpec(_HITLContract):
    """Immutable question definition used to validate a typed answer."""

    question_id: _NonBlankId
    interaction_kind: HITLInteractionKind
    prompt: _Prompt
    answer_kind: HITLAnswerKind
    required: StrictBool = True
    choices: list[_NonBlankId] | None = Field(
        default=None, min_length=2, max_length=100
    )

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        choice_kinds = {
            HITLAnswerKind.SINGLE_CHOICE,
            HITLAnswerKind.MULTI_CHOICE,
        }
        if self.answer_kind in choice_kinds:
            if self.choices is None:
                raise ValueError("choice questions require a choice inventory")
            if len(set(self.choices)) != len(self.choices):
                raise ValueError("question choices must be unique")
        elif self.choices is not None:
            raise ValueError("only choice questions may define choices")

        permitted_by_interaction = {
            HITLInteractionKind.QUESTIONNAIRE: {
                HITLAnswerKind.TEXT,
                HITLAnswerKind.SINGLE_CHOICE,
                HITLAnswerKind.MULTI_CHOICE,
                HITLAnswerKind.CONFIRMATION,
            },
            HITLInteractionKind.AUTH_CHALLENGE: {HITLAnswerKind.AUTHORIZATION_RESULT},
            HITLInteractionKind.POLICY_DECISION: {HITLAnswerKind.POLICY_DECISION},
        }
        if self.answer_kind not in permitted_by_interaction[self.interaction_kind]:
            raise ValueError(
                f"{self.interaction_kind.value} does not accept "
                f"{self.answer_kind.value} answers"
            )
        return self

    def validate_answer(self, answer: HITLQuestionAnswer) -> None:
        """Validate answer identity, kind, and selections against this question."""

        if answer.question_id != self.question_id:
            raise ValueError("answer question_id does not match question")
        if answer.answer.kind != self.answer_kind:
            raise ValueError("answer kind does not match question")
        if isinstance(answer.answer, HITLSingleChoiceAnswer):
            if self.choices is None or answer.answer.choice not in self.choices:
                raise ValueError("answer choice is not in the question inventory")
        if isinstance(answer.answer, HITLMultiChoiceAnswer):
            inventory = set(self.choices or ())
            if not set(answer.answer.choices).issubset(inventory):
                raise ValueError("answer choices are not in the question inventory")


class A2AInteractionSpec(_HITLContract):
    """Typed interaction advertised by a remote agent in A2A message metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1]
    interaction_id: _NonBlankId
    questions: list[HITLQuestionSpec] = Field(min_length=1, max_length=100)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_strict_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be the integer literal 1")
        return value

    @model_validator(mode="after")
    def validate_unique_question_ids(self) -> Self:
        question_ids = [question.question_id for question in self.questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("question_id values must be unique")
        return self


class HITLQuestionAnswer(_HITLContract):
    question_id: _NonBlankId
    answer: HITLAnswer


class HITLCancelCommand(_HITLContract):
    interaction_id: _NonBlankId
    expected_interaction_version: _StrictVersion
    client_request_id: _NonBlankId
    reason: _ShortReason | None = None


class HITLRouteSnapshot(_HITLContract):
    """Immutable application target captured when an interaction is created."""

    schema_version: Literal[1] = 1
    route: HITLApplicationRoute
    orchestration_run_id: _NonBlankId | None = None
    task_id: _NonBlankId | None = None
    context_id: _NonBlankId | None = None
    continuation_message_id: _NonBlankId | None = None
    agent_id: _NonBlankId | None = None

    @model_validator(mode="after")
    def validate_route(self) -> Self:
        a2a_fields = (
            "task_id",
            "context_id",
            "continuation_message_id",
            "agent_id",
        )
        if self.route == HITLApplicationRoute.SUPERVISOR_RUN:
            if self.orchestration_run_id is None:
                raise ValueError("supervisor_run requires orchestration_run_id")
            if any(getattr(self, field_name) is not None for field_name in a2a_fields):
                raise ValueError("supervisor_run must not include an A2A target")
            return self

        if self.orchestration_run_id is not None:
            raise ValueError("a2a_resume must not include orchestration_run_id")
        required_a2a_fields = (
            "task_id",
            "context_id",
            "continuation_message_id",
            "agent_id",
        )
        for field_name in required_a2a_fields:
            value = getattr(self, field_name)
            if value is None:
                raise ValueError(f"a2a_resume requires {field_name}")
            if _is_provisional_identifier(value):
                raise ValueError(
                    f"{field_name} must be authoritative and non-provisional"
                )
        return self

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the snapshot's canonical JSON representation."""

        canonical_json = json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical_json.encode()).hexdigest()


class HITLRouteSnapshotV2(_HITLContract):
    """Invocation-owned route added for the orchestrator runtime."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2] = 2
    route: Literal["orchestrator_tool"] = "orchestrator_tool"
    orchestration_run_id: _NonBlankId
    call_record_id: _NonBlankId
    invocation_id: _NonBlankId
    room_id: _NonBlankId
    room_epoch: _StrictVersion
    binding_id: _NonBlankId
    agent_id: _NonBlankId
    task_id: _NonBlankId | None = None
    context_id: _NonBlankId | None = None
    interaction_revision: _StrictVersion
    interaction_fingerprint: _NonBlankId

    @model_validator(mode="after")
    def validate_authoritative_aliases(self) -> Self:
        for value in (self.task_id, self.context_id):
            if value is not None and _is_provisional_identifier(value):
                raise ValueError("V2 route aliases must be authoritative")
        return self

    @property
    def fingerprint(self) -> str:
        canonical_json = json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical_json.encode()).hexdigest()


HITLRouteSnapshotUnion = Annotated[
    HITLRouteSnapshot | HITLRouteSnapshotV2,
    Field(discriminator="schema_version"),
]


def _is_provisional_identifier(value: str) -> bool:
    normalized = value.casefold()
    return normalized in {"pending", "provisional", "unknown"} or normalized.startswith(
        ("pending-", "relay-pending-", "provisional-")
    )


__all__ = [
    "A2AInteractionSpec",
    "HITLAnswer",
    "HITLAnswerKind",
    "HITLApplicationRoute",
    "HITLAuthorizationResultAnswer",
    "HITLCancelCommand",
    "HITLConfirmationAnswer",
    "HITLEvidenceOrigin",
    "HITLInteractionKind",
    "HITLMultiChoiceAnswer",
    "HITLPolicyDecision",
    "HITLPolicyDecisionAnswer",
    "HITLPublicSource",
    "HITLQuestionAnswer",
    "HITLQuestionSpec",
    "HITLRouteSnapshot",
    "HITLRouteSnapshotUnion",
    "HITLRouteSnapshotV2",
    "HITLSingleChoiceAnswer",
    "HITLTextAnswer",
]
