from __future__ import annotations

from collections.abc import Mapping
import re

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
    if _answer_is_insufficient(answer_text):
        return
    answer_fact_id = str(answer_fact.get("fact_id") or "").strip()
    for question in state.open_questions:
        if not isinstance(question, Mapping):
            continue
        if question.get("request_id") not in resolved_request_ids:
            continue
        if question.get("resolved") is not True:
            continue
        blocker_keys = question.get("blocker_keys") or []
        shared_obligations = question.get("required_obligation_keys") or []
        blocker_obligations = question.get("blocker_obligations")
        for blocker in state.blockers:
            if blocker.key not in blocker_keys or blocker.status != "open":
                continue
            if blocker_obligations is None:
                obligations = shared_obligations
            elif isinstance(blocker_obligations, Mapping):
                obligations = blocker_obligations.get(blocker.key)
            else:
                obligations = None
            if not _answer_satisfies_obligations(answer_text, obligations):
                continue
            blocker.status = "resolved"
            if answer_fact_id and answer_fact_id not in blocker.evidence_refs:
                blocker.evidence_refs.append(answer_fact_id)


def _answer_satisfies_obligations(answer_text: str, obligations: object) -> bool:
    if not isinstance(obligations, list) or not obligations:
        return False
    if _answer_is_insufficient(answer_text):
        return False
    normalized = _normalize_answer_text(answer_text)
    for obligation in obligations:
        if not isinstance(obligation, str) or ":" not in obligation:
            return False
        field_key = obligation.split(":", 1)[1]
        if field_key == "$present":
            continue
        field_tokens = {
            token
            for token in field_key.replace(".", "_").split("_")
            if token and token not in {"requested"}
        }
        if field_tokens and not field_tokens <= normalized:
            return False
        if field_tokens and not _answer_has_field_value(normalized, field_tokens):
            return False
    return True


def _answer_is_insufficient(answer_text: str) -> bool:
    normalized_answer = " ".join(answer_text.lower().split())
    return any(marker in normalized_answer for marker in _INSUFFICIENT_ANSWER_MARKERS)


def _answer_has_field_value(
    answer_tokens: set[str], field_tokens: set[str]
) -> bool:
    return bool(answer_tokens - field_tokens - _ANSWER_VALUE_FILLER - {"requested"})


def _normalize_answer_text(answer_text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", answer_text.lower()))


def _matched_output_keys(
    blocker: BlockerRecord,
    obligations: list[str],
    required_output_keys: set[str],
) -> set[str]:
    if blocker.blocked_output_keys:
        return set(blocker.blocked_output_keys) & required_output_keys
    normalized_text = _normalize_match_text(f"{blocker.key} {blocker.description}")
    blocker_tokens = _match_tokens(normalized_text)
    matched: set[str] = set()
    for obligation in obligations:
        output_key, field_key = _split_obligation(obligation)
        if output_key not in required_output_keys:
            continue
        if _normalize_match_text(output_key) in normalized_text:
            matched.add(output_key)
            continue
        if field_key and _normalize_match_text(field_key) in normalized_text:
            matched.add(output_key)
            continue
        if field_key and _match_tokens(field_key) <= blocker_tokens:
            matched.add(output_key)
    return matched


def _split_obligation(obligation: str) -> tuple[str, str | None]:
    if ":$present" in obligation:
        return obligation.split(":$present", 1)[0], None
    if ":" in obligation:
        output_key, field_key = obligation.split(":", 1)
        return output_key, field_key
    return obligation, None


def _normalize_match_text(value: str) -> str:
    return value.lower().replace(".", "_").replace("-", "_").replace(" ", "_")


def _match_tokens(value: str) -> set[str]:
    return {token for token in _normalize_match_text(value).split("_") if token}


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
