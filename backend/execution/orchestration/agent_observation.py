from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from models.orchestration import BlockerRecord, UnknownRecord

MISSING_LIST_KEYS = {
    "missing",
    "missing_fields",
    "missing_items",
    "missing_required_fields",
    "unknown_fields",
    "unknowns",
}


@dataclass(frozen=True)
class AgentObservation:
    facts: list[dict[str, Any]]
    unknowns: list[UnknownRecord]
    blocker_candidates: list[BlockerRecord]


def extract_agent_observation(  # noqa: C901
    *,
    agent_message_id: str,
    agent_id: str,
    status: str,
    text: str | None,
    status_message: str | None,
    artifact_records: Sequence[Mapping[str, Any]],
) -> AgentObservation:
    facts: list[dict[str, Any]] = []
    unknowns_by_key: dict[str, UnknownRecord] = {}
    blockers_by_key: dict[str, BlockerRecord] = {}

    for artifact in artifact_records:
        artifact_key = str(artifact.get("artifact_key") or "")
        artifact_name = str(artifact.get("name") or artifact.get("summary") or "artifact")
        evidence_refs = [artifact_key] if artifact_key else []
        for data in _iter_data_parts(artifact):
            for path, value in _flatten_data(data):
                if _is_missing_list_path(path):
                    for missing_path in _coerce_missing_items(value):
                        _record_missing(
                            unknowns_by_key,
                            blockers_by_key,
                            agent_message_id=agent_message_id,
                            source_agent_message_id=agent_message_id,
                            source_agent_id=agent_id,
                            missing_key=missing_path,
                            description=f"Agent reported missing input: {missing_path}",
                            evidence_refs=evidence_refs,
                        )
                    continue
                if value is None:
                    _record_missing(
                        unknowns_by_key,
                        blockers_by_key,
                        agent_message_id=agent_message_id,
                        source_agent_message_id=agent_message_id,
                        source_agent_id=agent_id,
                        missing_key=path,
                        description=f"Agent result has no value for {path}.",
                        evidence_refs=evidence_refs,
                    )
                    continue
                facts.append(
                    {
                        "fact_id": f"{agent_message_id}:{artifact_name}:{path}",
                        "kind": "agent_observation",
                        "semantic_key": (
                            f"agent_observation:{agent_message_id}:"
                            f"{artifact_name}:{path}"
                        ),
                        "value": value,
                        "source_agent_message_id": agent_message_id,
                        "source_agent_id": agent_id,
                        "evidence_refs": evidence_refs,
                    }
                )

    text_evidence = _nonempty(status_message) or _nonempty(text)
    if text_evidence is not None and not artifact_records:
        facts.append(
            {
                "fact_id": f"{agent_message_id}:text_evidence",
                "kind": "agent_text_evidence",
                "semantic_key": f"agent_text_evidence:{agent_message_id}",
                "value": text_evidence,
                "source_agent_message_id": agent_message_id,
                "source_agent_id": agent_id,
                "evidence_refs": [agent_message_id, f"{agent_message_id}:text_or_status"],
                "trusted_for_blocker_keys": False,
            }
        )

    if status != "awaiting_input":
        missing_input = _parse_missing_input(status_message)
        if missing_input is not None:
            missing_key, missing_description = missing_input
            _record_missing(
                unknowns_by_key,
                blockers_by_key,
                agent_message_id=agent_message_id,
                source_agent_message_id=agent_message_id,
                source_agent_id=agent_id,
                missing_key=missing_key,
                description=missing_description,
                evidence_refs=[agent_message_id, f"{agent_message_id}:text_or_status"],
            )

    if status == "awaiting_input":
        message = _nonempty(status_message) or _nonempty(text) or "Agent requested additional input."
        _record_missing(
            unknowns_by_key,
            blockers_by_key,
            agent_message_id=agent_message_id,
            source_agent_message_id=agent_message_id,
            source_agent_id=agent_id,
            missing_key="agent_input_required",
            description=message,
            evidence_refs=[agent_message_id, f"{agent_message_id}:awaiting_input"],
        )

    return AgentObservation(
        facts=sorted(facts, key=lambda item: item["fact_id"]),
        unknowns=sorted(unknowns_by_key.values(), key=lambda item: item.key),
        blocker_candidates=sorted(blockers_by_key.values(), key=lambda item: item.key),
    )


def _record_missing(
    unknowns_by_key: dict[str, UnknownRecord],
    blockers_by_key: dict[str, BlockerRecord],
    *,
    agent_message_id: str,
    source_agent_message_id: str,
    source_agent_id: str,
    missing_key: str,
    description: str,
    evidence_refs: list[str],
) -> None:
    normalized = _normalize_missing_key(missing_key)
    unknown_key = f"agent_missing:{source_agent_id}:{normalized}"
    blocker_key = f"agent_blocker:{source_agent_id}:{normalized}"
    unknowns_by_key[unknown_key] = UnknownRecord(
        key=unknown_key,
        description=description,
        source_agent_message_id=source_agent_message_id,
    )
    blockers_by_key[blocker_key] = BlockerRecord(
        key=blocker_key,
        description=description,
        source="agent",
        evidence_refs=list(evidence_refs),
        claimed_user_only=False,
        validated_user_only=False,
        validation_status="candidate",
        status="open",
    )


def _iter_data_parts(artifact: Mapping[str, Any]) -> Iterable[Any]:
    parts = artifact.get("parts", [])
    if not isinstance(parts, list):
        return
    for part in parts:
        if isinstance(part, Mapping) and "data" in part:
            yield part["data"]


def _flatten_data(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _flatten_data(item, path)
        return
    yield prefix, value


def _coerce_missing_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _is_missing_list_path(path: str) -> bool:
    tail = path.rsplit(".", 1)[-1]
    return tail in MISSING_LIST_KEYS


def _normalize_missing_key(value: str) -> str:
    return ".".join(segment for segment in value.strip().replace(" ", "_").split(".") if segment)


def _nonempty(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_missing_input(value: str | None) -> tuple[str, str] | None:
    normalized = _nonempty(value)
    if normalized is None:
        return None
    match = re.fullmatch(
        r"need\s+(?:the\s+)?(.+?)(?:\s+before\s+continuing)?[.!?]?",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    description = match.group(1).strip()
    if not description:
        return None
    return (
        _normalize_missing_key(description),
        f"Agent text indicates missing input: {description}",
    )
