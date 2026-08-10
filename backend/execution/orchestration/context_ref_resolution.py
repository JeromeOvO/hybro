"""Resolve planner context_refs to durable run-state fact IDs.

Planners sometimes emit symbolic refs such as expected-output keys (``story_text``)
or pair those keys with ``source_agent_message_id``. Dispatch and validation need
a shared resolver so retries and sequential steps can materialize the real
``{message_id}:text_evidence`` facts produced by upstream Agents.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from models.orchestration import (
    AgentOutputRecord,
    DispatchContentRef,
    DispatchExpectedOutput,
    DispatchIntent,
    OrchestrationRunState,
)


def text_evidence_fact_id(agent_message_id: str) -> str:
    return f"{agent_message_id}:text_evidence"


def fact_ids_in_state(run_state: OrchestrationRunState) -> set[str]:
    return {
        str(fact.get("fact_id"))
        for fact in run_state.facts
        if isinstance(fact, Mapping) and fact.get("fact_id") is not None
    }


def artifact_keys_in_state(run_state: OrchestrationRunState) -> set[str]:
    return {
        str(artifact.get("artifact_key"))
        for artifact in run_state.artifacts
        if isinstance(artifact, dict) and artifact.get("artifact_key") is not None
    }


def resolve_context_ref_to_fact_id(
    run_state: OrchestrationRunState,
    ref: DispatchContentRef,
) -> str | None:
    """Map a context ref to an existing fact_id when possible.

    Resolution order:
    1. Exact ``ref_id`` already present as a fact.
    2. ``source_agent_message_id`` → ``{id}:text_evidence`` fact.
    3. ``ref_id`` matching a satisfied text-compatible expected-output key from a
       fulfilled prior dispatch → that producer's text-evidence fact.
    4. ``source_agent_message_id`` / text output-key match against successful
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
        _producer_message_id_for_text_output_key(run_state, ref_id) if ref_id else None
    )
    candidate = _text_evidence_if_present(fact_ids, producer_message_id)
    if candidate is not None:
        return candidate

    for message_id in (source_message_id, producer_message_id):
        if message_id and _can_materialize_text_evidence(run_state, message_id):
            return text_evidence_fact_id(message_id)
    return None


def resolve_context_ref_to_artifact_key(
    run_state: OrchestrationRunState,
    ref: DispatchContentRef,
) -> str | None:
    """Map a symbolic output-key ref to a durable artifact key when possible."""

    ref_id = (ref.ref_id or "").strip()
    if not ref_id:
        return None
    artifact_keys = artifact_keys_in_state(run_state)
    if ref_id in artifact_keys:
        return ref_id
    binding = _fulfilled_output_binding(run_state, ref_id)
    if binding is None:
        return None
    _intent, expected_output, agent_output = binding
    if _is_text_compatible_output(expected_output):
        return None
    artifacts = _matching_output_artifacts(run_state, expected_output, agent_output)
    if not artifacts:
        return None
    artifact_key = artifacts[0].get("artifact_key")
    return str(artifact_key) if artifact_key is not None else None


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
    if not message_id or not _can_materialize_text_evidence(run_state, message_id):
        return None
    text = _agent_output_text(run_state, message_id)
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
        artifact_key = resolve_context_ref_to_artifact_key(run_state, ref)
        if artifact_key is not None and artifact_key != ref.ref_id:
            changed = True
            rewritten.append(ref.model_copy(update={"ref_id": artifact_key}, deep=True))
            continue
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


def _producer_message_id_for_text_output_key(
    run_state: OrchestrationRunState,
    output_key: str,
) -> str | None:
    binding = _fulfilled_output_binding(run_state, output_key)
    if binding is None:
        return None
    _intent, expected_output, _agent_output = binding
    if not _is_text_compatible_output(expected_output):
        return None
    return binding[0].planned_agent_message_id


def _fulfilled_output_binding(
    run_state: OrchestrationRunState,
    output_key: str,
) -> tuple[DispatchIntent, DispatchExpectedOutput, AgentOutputRecord] | None:
    intents_by_id = {
        intent.dispatch_intent_id: intent for intent in run_state.dispatch_intents
    }
    outputs_by_message_id = {
        output.agent_message_id: output
        for output in run_state.agent_outputs
        if output.status in {"success", "completed", "fulfilled"}
    }
    for outcome in reversed(run_state.delegation_outcomes):
        if outcome.status != "fulfilled":
            continue
        intent = intents_by_id.get(outcome.dispatch_intent_id)
        if intent is None:
            continue
        if not _outcome_covers_output_key(outcome, intent, output_key):
            continue
        agent_output = outputs_by_message_id.get(intent.planned_agent_message_id)
        if agent_output is None:
            return None
        expected_output = _expected_output_for_key(intent, output_key)
        if expected_output is None:
            return None
        return intent, expected_output, agent_output
    return None


def _expected_output_for_key(
    intent: DispatchIntent,
    output_key: str,
) -> DispatchExpectedOutput | None:
    for output in intent.expected_outputs:
        if output.output_key == output_key:
            return output
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


def _is_text_compatible_output(output: DispatchExpectedOutput) -> bool:
    kind = (output.kind or "").strip().lower()
    return kind in {
        "text",
        "text/plain",
        "markdown",
        "text/markdown",
    } or kind.startswith("text/")


def _can_materialize_text_evidence(
    run_state: OrchestrationRunState,
    message_id: str,
) -> bool:
    if not _agent_output_text(run_state, message_id):
        return False
    for intent in run_state.dispatch_intents:
        if intent.planned_agent_message_id != message_id:
            continue
        if not intent.expected_outputs:
            return True
        if any(
            _is_text_compatible_output(output) for output in intent.expected_outputs
        ):
            return True
    return False


def _matching_output_artifacts(
    run_state: OrchestrationRunState,
    expected_output: DispatchExpectedOutput,
    agent_output: AgentOutputRecord,
) -> list[dict[str, Any]]:
    artifact_keys = set(agent_output.artifact_keys)
    owned_artifacts = [
        artifact
        for artifact in run_state.artifacts
        if isinstance(artifact, dict)
        and artifact.get("artifact_key") in artifact_keys
        and _artifact_matches_output(artifact, expected_output)
    ]
    if not expected_output.artifact_name:
        return owned_artifacts
    return [
        artifact
        for artifact in owned_artifacts
        if artifact.get("name") == expected_output.artifact_name
    ]


def _artifact_matches_output(
    artifact: dict[str, Any], expected_output: DispatchExpectedOutput
) -> bool:
    kind = (expected_output.kind or "").strip().lower()
    if kind in {"artifact", "file"}:
        return True
    mime_types = _artifact_mime_types(artifact)
    if not mime_types:
        return False
    if kind in {"image", "audio", "video"}:
        return any(mime_type.startswith(f"{kind}/") for mime_type in mime_types)
    if kind.endswith("/*"):
        return any(
            mime_type.startswith(kind.removesuffix("*")) for mime_type in mime_types
        )
    return kind in mime_types


def _artifact_mime_types(artifact: dict[str, Any]) -> set[str]:
    mime_types: set[str] = set()
    artifact_mime = artifact.get("mime_type") or artifact.get("mimeType")
    if isinstance(artifact_mime, str) and artifact_mime.strip():
        mime_types.add(artifact_mime.strip().lower())
    for part in artifact.get("parts", []):
        if not isinstance(part, dict):
            continue
        file_info = part.get("file")
        if isinstance(file_info, dict):
            mime_type = file_info.get("mimeType") or file_info.get("mime_type")
            if isinstance(mime_type, str) and mime_type.strip():
                mime_types.add(mime_type.strip().lower())
        metadata = part.get("metadata")
        if isinstance(metadata, dict):
            mime_type = metadata.get("mime_type") or metadata.get("mimeType")
            if isinstance(mime_type, str) and mime_type.strip():
                mime_types.add(mime_type.strip().lower())
        if part.get("data") not in (None, {}, [], ""):
            mime_types.add("application/json")
    return mime_types


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
    "artifact_keys_in_state",
    "context_ref_text_payload",
    "fact_ids_in_state",
    "resolve_context_ref_to_artifact_key",
    "resolve_context_ref_to_fact_id",
    "rewrite_context_refs_with_available_facts",
    "text_evidence_fact_id",
]
