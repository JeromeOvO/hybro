from __future__ import annotations

import re
from collections.abc import Mapping

from execution.orchestration.blocker_matching import (
    match_tokens,
    normalize_match_text,
)
from execution.orchestration.outcome_policy import BlockerPolicyValidator
from models.orchestration import (
    BlockerRecord,
    BlockerResolutionAttempt,
    DelegationOutcomeRecord,
    DispatchIntent,
    OrchestrationRunState,
)


def resolve_agent_observed_blockers(
    state: OrchestrationRunState,
    *,
    intent: DispatchIntent,
    outcome: DelegationOutcomeRecord,
    available_resource_refs: set[str] | None,
    attempted_agent_ids: set[str] | None,
    eligible_alternate_agent_ids: set[str] | None,
    conditional_result_viable: bool,
) -> tuple[OrchestrationRunState, DelegationOutcomeRecord]:
    required_output_keys = _output_keys_from_obligations(
        outcome.remaining_required_obligations
    )
    if not required_output_keys:
        return state, outcome

    updated = state.model_copy(deep=True)
    validator = BlockerPolicyValidator()
    validated: list[BlockerRecord] = []
    sanitized = False
    evidence_refs = {
        *outcome.changed_artifact_keys,
        intent.planned_agent_message_id,
        f"{intent.planned_agent_message_id}:awaiting_input",
    }
    for index, blocker in enumerate(updated.blockers):
        if blocker.status != "open":
            continue
        if blocker.source != "agent":
            continue
        if not set(blocker.evidence_refs) & evidence_refs:
            continue
        blocked_keys = _matched_output_keys(
            blocker,
            outcome.remaining_required_obligations,
            required_output_keys,
        )
        if not blocked_keys:
            continue
        candidate = blocker.model_copy(
            update={
                "blocked_output_keys": sorted(blocked_keys),
                "claimed_user_only": True,
                "validation_status": "candidate",
                "resolution_attempts": _resolution_attempts(
                    intent,
                    blocked_keys,
                    attempted_agent_ids=attempted_agent_ids,
                ),
            }
        )
        decision = validator.validate(
            candidate,
            required_output_keys=required_output_keys,
            available_resource_refs=available_resource_refs,
            eligible_alternate_agent_ids=eligible_alternate_agent_ids,
            conditional_result_viable=conditional_result_viable,
        )
        if not decision.valid:
            updated.blockers[index] = candidate.model_copy(
                update={"validated_user_only": False},
                deep=True,
            )
            sanitized = True
            continue
        replacement = candidate.model_copy(
            update={
                "validated_user_only": True,
                "validation_status": "validated",
            },
            deep=True,
        )
        updated.blockers[index] = replacement
        validated.append(replacement)

    if not validated:
        return (updated if sanitized else state), outcome

    updated_outcome = outcome.model_copy(
        update={
            "status": "blocked",
            "blockers": sorted(validated, key=lambda item: item.key),
        }
    )
    return updated, updated_outcome


_INSUFFICIENT_ANSWER_MARKERS = (
    "i do not know",
    "i don't know",
    "unknown",
    "not sure",
    "skip",
    "n/a",
)

_ANSWER_VALUE_FILLER = {
    "a",
    "an",
    "are",
    "be",
    "is",
    "of",
    "the",
    "to",
}

_NON_VALUE_ANSWER_TOKENS = {
    "available",
    "confirmed",
    "pending",
}

_AMOUNT_LIKE_FIELD_TOKENS = {
    "amount",
    "cost",
    "deductible",
    "limit",
    "premium",
    "price",
    "value",
}


def validate_hitl_answered_blockers(
    state: OrchestrationRunState,
    *,
    resolved_request_ids: set[str],
    answer_fact: Mapping[str, object],
) -> None:
    if not resolved_request_ids:
        return
    answer_text = str(answer_fact.get("text") or "").strip()
    if not answer_text:
        return
    answer_fact_id = str(answer_fact.get("fact_id") or "").strip()
    for question in state.open_questions:
        if not _is_resolved_hitl_question(question, resolved_request_ids):
            continue
        _resolve_answered_question_blockers(
            state,
            question=question,
            answer_text=answer_text,
            answer_fact_id=answer_fact_id,
        )


def _is_resolved_hitl_question(
    question: object,
    resolved_request_ids: set[str],
) -> bool:
    return (
        isinstance(question, Mapping)
        and question.get("request_id") in resolved_request_ids
        and question.get("resolved") is True
    )


def _resolve_answered_question_blockers(
    state: OrchestrationRunState,
    *,
    question: Mapping[str, object],
    answer_text: str,
    answer_fact_id: str,
) -> None:
    blocker_keys = question.get("blocker_keys") or []
    allow_value_only = len(blocker_keys) == 1
    for blocker in state.blockers:
        if blocker.key not in blocker_keys or blocker.status != "open":
            continue
        obligations = _question_obligations(state, question, blocker)
        if not _answer_satisfies_obligations(
            answer_text,
            obligations,
            allow_value_only=allow_value_only,
        ):
            continue
        blocker.status = "resolved"
        if answer_fact_id and answer_fact_id not in blocker.evidence_refs:
            blocker.evidence_refs.append(answer_fact_id)


def _question_obligations(
    state: OrchestrationRunState,
    question: Mapping[str, object],
    blocker: BlockerRecord,
) -> object:
    blocker_obligations = question.get("blocker_obligations")
    if isinstance(blocker_obligations, Mapping) and blocker_obligations:
        return blocker_obligations.get(blocker.key)
    shared_obligations = question.get("required_obligation_keys")
    if isinstance(shared_obligations, list) and shared_obligations:
        return shared_obligations
    return _required_obligations_for_blocker(state, blocker)


def _required_obligations_for_blocker(
    state: OrchestrationRunState,
    blocker: BlockerRecord,
) -> list[str]:
    blocked_outputs = set(blocker.blocked_output_keys)
    for outcome in reversed(state.delegation_outcomes):
        obligations = [
            obligation
            for obligation in outcome.remaining_required_obligations
            if not blocked_outputs or obligation.split(":", 1)[0] in blocked_outputs
        ]
        if obligations:
            return sorted(dict.fromkeys(obligations))
    return []


def _answer_satisfies_obligations(
    answer_text: str,
    obligations: object,
    *,
    allow_value_only: bool,
) -> bool:
    if obligations is None or not isinstance(obligations, list):
        return False
    if not obligations:
        return not _answer_is_insufficient(answer_text)
    answer_segments = _answer_segments(answer_text)
    for obligation in obligations:
        if not isinstance(obligation, str) or ":" not in obligation:
            return False
        output_key, field_key = obligation.split(":", 1)
        match_key = output_key if field_key == "$present" else field_key
        field_tokens = {
            token
            for token in match_key.replace(".", "_").split("_")
            if token and token not in {"requested"}
        }
        matching_segments = [
            segment
            for segment in answer_segments
            if field_tokens <= _normalize_answer_text(segment)
        ]
        if not matching_segments and allow_value_only:
            matching_segments = [answer_text]
        if not matching_segments:
            return False
        if not any(
            not _answer_is_insufficient(segment)
            and _answer_has_field_value(
                _normalize_answer_text(segment),
                field_tokens,
            )
            for segment in matching_segments
        ):
            return False
    return True


def _answer_is_insufficient(answer_text: str) -> bool:
    answer_tokens = re.findall(r"[a-z0-9]+", answer_text.lower())
    return any(
        _contains_token_sequence(
            answer_tokens,
            re.findall(r"[a-z0-9]+", marker.lower()),
        )
        for marker in _INSUFFICIENT_ANSWER_MARKERS
    )


def _contains_token_sequence(tokens: list[str], sequence: list[str]) -> bool:
    if not sequence:
        return False
    width = len(sequence)
    return any(
        tokens[index : index + width] == sequence
        for index in range(len(tokens) - width + 1)
    )


def _answer_segments(answer_text: str) -> list[str]:
    return [
        segment.strip()
        for segment in re.split(
            r"(?:[.;\n]+|\b(?:and|but)\b)",
            answer_text,
            flags=re.IGNORECASE,
        )
        if segment.strip()
    ]


def _answer_has_field_value(answer_tokens: set[str], field_tokens: set[str]) -> bool:
    value_tokens = (
        answer_tokens
        - field_tokens
        - _ANSWER_VALUE_FILLER
        - _NON_VALUE_ANSWER_TOKENS
        - {"requested"}
    )
    if not value_tokens:
        return False
    if field_tokens & _AMOUNT_LIKE_FIELD_TOKENS:
        return any(
            any(character.isdigit() for character in token) for token in value_tokens
        )
    return True


def _normalize_answer_text(answer_text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", answer_text.lower()))


def _matched_output_keys(
    blocker: BlockerRecord,
    obligations: list[str],
    required_output_keys: set[str],
) -> set[str]:
    if blocker.blocked_output_keys:
        return set(blocker.blocked_output_keys) & required_output_keys
    normalized_text = normalize_match_text(f"{blocker.key} {blocker.description}")
    blocker_tokens = match_tokens(normalized_text)
    matched: set[str] = set()
    for obligation in obligations:
        output_key, field_key = _split_obligation(obligation)
        if output_key not in required_output_keys:
            continue
        if normalize_match_text(output_key) in normalized_text:
            matched.add(output_key)
            continue
        if field_key and normalize_match_text(field_key) in normalized_text:
            matched.add(output_key)
            continue
        if field_key and match_tokens(field_key) <= blocker_tokens:
            matched.add(output_key)
    return matched


def _split_obligation(obligation: str) -> tuple[str, str | None]:
    if ":$present" in obligation:
        return obligation.split(":$present", 1)[0], None
    if ":" in obligation:
        output_key, field_key = obligation.split(":", 1)
        return output_key, field_key
    return obligation, None


def _output_keys_from_obligations(obligations: list[str]) -> set[str]:
    keys: set[str] = set()
    for obligation in obligations:
        if ":$present" in obligation:
            keys.add(obligation.split(":$present", 1)[0])
            continue
        if ":" in obligation:
            keys.add(obligation.split(":", 1)[0])
            continue
        if obligation:
            keys.add(obligation)
    return keys


def _resolution_attempts(
    intent: DispatchIntent,
    output_keys: set[str],
    *,
    attempted_agent_ids: set[str] | None,
) -> list[BlockerResolutionAttempt]:
    applies_to_output_keys = sorted(output_keys)
    attempts: list[BlockerResolutionAttempt] = []
    for ref in [*intent.context_refs, *intent.artifact_refs, *intent.attachment_refs]:
        attempts.append(
            BlockerResolutionAttempt(
                kind="resource",
                reference_id=ref.ref_id,
                outcome="insufficient",
                applies_to_output_keys=applies_to_output_keys,
            )
        )
    for agent_id in sorted(attempted_agent_ids or {intent.agent_id}):
        attempts.append(
            BlockerResolutionAttempt(
                kind="agent",
                reference_id=agent_id,
                outcome="insufficient",
                applies_to_output_keys=applies_to_output_keys,
            )
        )
    attempts.append(
        BlockerResolutionAttempt(
            kind="conditional_result",
            reference_id=intent.dispatch_intent_id,
            outcome="insufficient",
            applies_to_output_keys=applies_to_output_keys,
        )
    )
    return attempts
