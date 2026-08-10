"""Resolve planner context_refs to durable run-state fact IDs.

Planners sometimes emit symbolic refs such as expected-output keys (``story_text``)
or pair those keys with ``source_agent_message_id``. Dispatch and validation need
a shared resolver so retries and sequential steps can materialize the real
``{message_id}:text_evidence`` facts produced by upstream Agents.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from models.orchestration import DispatchContentRef, OrchestrationRunState


def text_evidence_fact_id(agent_message_id: str) -> str:
    return f"{agent_message_id}:text_evidence"


def fact_ids_in_state(run_state: OrchestrationRunState) -> set[str]:
    return {
        str(fact.get("fact_id"))
        for fact in run_state.facts
        if isinstance(fact, Mapping) and fact.get("fact_id") is not None
    }


def resolve_context_ref_to_fact_id(
    run_state: OrchestrationRunState,
    ref: DispatchContentRef,
) -> str | None:
    """Map a context ref to an existing fact_id when possible.

    Resolution order:
    1. Exact ``ref_id`` already present as a fact.
    2. ``source_agent_message_id`` → ``{id}:text_evidence`` fact.
    3. ``ref_id`` matching a satisfied expected-output key from a fulfilled
       prior dispatch → that producer's text-evidence fact.
    4. ``source_agent_message_id`` / output-key match against successful
       ``agent_outputs`` even when the fact row is missing (caller may build
       a payload from the output text).
    """

    fact_ids = fact_ids_in_state(run_state)
    ref_id = (ref.ref_id or "").strip()
    if ref_id and ref_id in fact_ids:
        return ref_id

    source_message_id = (ref.source_agent_message_id or "").strip() or None
    candidate = _text_evidence_if_present(fact_ids, source_message_id)
    if candidate is not None:
        return candidate

    producer_message_id = (
        _producer_message_id_for_output_key(run_state, ref_id) if ref_id else None
    )
    candidate = _text_evidence_if_present(fact_ids, producer_message_id)
    if candidate is not None:
        return candidate

    for message_id in (source_message_id, producer_message_id):
        if message_id and _agent_output_text(run_state, message_id):
            return text_evidence_fact_id(message_id)
    return None


def context_ref_text_payload(
    run_state: OrchestrationRunState,
    ref: DispatchContentRef,
    *,
    resolved_fact_id: str | None = None,
) -> dict[str, Any] | None:
    """Return a context resource payload dict when text can be materialized."""

    fact_id = resolved_fact_id or resolve_context_ref_to_fact_id(run_state, ref)
    if fact_id is None:
        return None

    fact_payload = _payload_from_fact(run_state, ref, fact_id)
    if fact_payload is not None:
        return fact_payload

    message_id = (ref.source_agent_message_id or "").strip()
    if not message_id and fact_id.endswith(":text_evidence"):
        message_id = fact_id[: -len(":text_evidence")]
    text = _agent_output_text(run_state, message_id) if message_id else None
    if not text:
        return None
    return {
        "ref_id": fact_id,
        "kind": "context",
        "mime_type": ref.mime_type or "text/plain",
        "text": text,
        "metadata": {
            "source_agent_message_id": message_id or ref.source_agent_message_id,
            "aliased_from": ref.ref_id,
        },
    }


def rewrite_context_refs_with_available_facts(
    run_state: OrchestrationRunState,
    refs: list[DispatchContentRef],
) -> list[DispatchContentRef]:
    """Rewrite symbolic context refs to durable fact IDs when resolvable."""

    rewritten: list[DispatchContentRef] = []
    changed = False
    for ref in refs:
        fact_id = resolve_context_ref_to_fact_id(run_state, ref)
        if fact_id is None or fact_id == ref.ref_id:
            rewritten.append(ref)
            continue
        changed = True
        rewritten.append(
            ref.model_copy(
                update={
                    "ref_id": fact_id,
                    "source_agent_message_id": (
                        ref.source_agent_message_id
                        or (
                            fact_id[: -len(":text_evidence")]
                            if fact_id.endswith(":text_evidence")
                            else None
                        )
                    ),
                },
                deep=True,
            )
        )
    return rewritten if changed else refs


def _text_evidence_if_present(
    fact_ids: set[str],
    message_id: str | None,
) -> str | None:
    if not message_id:
        return None
    candidate = text_evidence_fact_id(message_id)
    return candidate if candidate in fact_ids else None


def _payload_from_fact(
    run_state: OrchestrationRunState,
    ref: DispatchContentRef,
    fact_id: str,
) -> dict[str, Any] | None:
    for fact in run_state.facts:
        if not isinstance(fact, Mapping):
            continue
        if str(fact.get("fact_id")) != fact_id:
            continue
        value = fact.get("value")
        text = fact.get("text")
        if not isinstance(text, str) and value is not None:
            text = str(value)
        if isinstance(text, str) and text.strip():
            return {
                "ref_id": fact_id,
                "kind": "context",
                "mime_type": ref.mime_type or "text/plain",
                "text": text,
                "summary": fact.get("summary"),
                "metadata": {
                    "source_agent_message_id": fact.get("source_agent_message_id")
                    or ref.source_agent_message_id,
                    "aliased_from": ref.ref_id,
                },
            }
    return None


def _producer_message_id_for_output_key(
    run_state: OrchestrationRunState,
    output_key: str,
) -> str | None:
    intents_by_id = {
        intent.dispatch_intent_id: intent for intent in run_state.dispatch_intents
    }
    for outcome in reversed(run_state.delegation_outcomes):
        if outcome.status != "fulfilled":
            continue
        intent = intents_by_id.get(outcome.dispatch_intent_id)
        if intent is None:
            continue
        if not _outcome_covers_output_key(outcome, intent, output_key):
            continue
        return intent.planned_agent_message_id
    return None


def _outcome_covers_output_key(outcome, intent, output_key: str) -> bool:
    if output_key in set(outcome.satisfied_output_keys):
        return True
    expected_keys = {
        output.output_key for output in intent.expected_outputs if output.output_key
    }
    if output_key not in expected_keys:
        return False
    return output_key not in set(outcome.missing_output_keys or [])


def _agent_output_text(
    run_state: OrchestrationRunState,
    agent_message_id: str | None,
) -> str | None:
    if not agent_message_id:
        return None
    for output in reversed(run_state.agent_outputs):
        if output.agent_message_id != agent_message_id:
            continue
        if output.status not in {"success", "completed", "fulfilled"}:
            continue
        text = (output.text or "").strip()
        if text:
            return text
    return None


__all__ = [
    "context_ref_text_payload",
    "fact_ids_in_state",
    "resolve_context_ref_to_fact_id",
    "rewrite_context_refs_with_available_facts",
    "text_evidence_fact_id",
]
