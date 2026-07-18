from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from models.orchestration import DispatchExpectedOutput

VOLATILE_KEYS = {
    "artifact_key",
    "source_agent_message_id",
    "source_agent_id",
    "message_id",
    "task_id",
    "context_id",
    "created_at",
    "updated_at",
}


def _stable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_value(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_stable_value(item) for item in value]
    if isinstance(value, str):
        return " ".join(value.split())
    return value


def canonical_content_fingerprint(value: Any) -> str:
    payload = json.dumps(
        _stable_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def semantic_fact_map(facts: list[dict[str, Any]]) -> dict[str, object]:
    return {
        str(fact["semantic_key"]): _stable_value(fact.get("value"))
        for fact in facts
        if isinstance(fact, dict)
        and fact.get("kind") != "agent_text"
        and fact.get("semantic_key")
    }


@dataclass(frozen=True)
class GoalFingerprints:
    goal_family_fingerprint: str
    evidence_fingerprint: str
    goal_revision_fingerprint: str
    attempt_fingerprint: str


def effective_output_key(output: DispatchExpectedOutput) -> str:
    if output.output_key is None:
        raise ValueError("expected output must have a normalized output_key")
    return output.output_key


def goal_fingerprints(
    *,
    agent_id: str,
    expected_outputs: list[DispatchExpectedOutput],
    selected_content_fingerprints: list[str],
    dependency_family_fingerprints: list[str],
    upstream_output_fingerprints: list[str],
) -> GoalFingerprints:
    contracts = [
        {
            "output_key": effective_output_key(output),
            "kind": output.kind,
            "artifact_name": output.artifact_name,
            "required_fields": sorted(output.required_fields),
            "description": (
                output.description
                if not output.artifact_name and not output.required_fields
                else None
            ),
        }
        for output in expected_outputs
    ]
    family = canonical_content_fingerprint(
        {
            "contracts": contracts,
            "dependency_families": sorted(dependency_family_fingerprints),
        }
    )
    evidence = canonical_content_fingerprint(
        {
            "selected_content": sorted(set(selected_content_fingerprints)),
            "upstream_outputs": sorted(set(upstream_output_fingerprints)),
        }
    )
    revision = canonical_content_fingerprint({"family": family, "evidence": evidence})
    attempt = canonical_content_fingerprint({"revision": revision, "agent": agent_id})
    return GoalFingerprints(
        goal_family_fingerprint=family,
        evidence_fingerprint=evidence,
        goal_revision_fingerprint=revision,
        attempt_fingerprint=attempt,
    )
