"""Normalize orchestration candidate scope snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import uuid4

from models.orchestration import (
    AuthorizationBasis,
    CandidateAgentSnapshot,
    CandidateScopeSnapshot,
)

AuthorizationKind = Literal[
    "room_member", "saved_group_member", "explicit_selection", "mention"
]


def normalize_candidate_scope(
    *,
    room_id: str,
    source: str,
    selected_agent_set: Mapping[str, Any] | Sequence[Any],
    group_id: str | None = None,
    selected_by_user_id: str | None = None,
    room_membership_version: str | None = None,
    group_version: str | None = None,
) -> CandidateScopeSnapshot:
    """Build a stable candidate scope snapshot from selected agent data."""

    agents = _candidate_agents_from_selected_set(selected_agent_set)
    return CandidateScopeSnapshot(
        snapshot_id=uuid4().hex,
        source=source,
        room_id=room_id,
        group_id=group_id,
        agent_ids=[agent.agent_id for agent in agents],
        agents=agents,
        room_membership_version=room_membership_version,
        group_version=group_version,
        authorization_basis=AuthorizationBasis(
            kind=_authorization_kind(source),
            room_id=room_id,
            group_id=group_id,
            selected_by_user_id=selected_by_user_id,
        ),
    )


def candidate_scope_from_legacy_envelope(
    *,
    room_id: str,
    envelope: Mapping[str, Any] | None,
    selected_agent_set: Mapping[str, Any] | Sequence[Any] | None = None,
) -> CandidateScopeSnapshot:
    """Build a candidate scope snapshot from legacy orchestration envelope fields."""

    raw_envelope = envelope or {}
    source = (
        _optional_str(raw_envelope.get("candidate_scope_mode"))
        or "explicit_selection"
    )
    group_id = _optional_str(raw_envelope.get("candidate_scope_group_id"))
    revision = _positive_int(raw_envelope.get("candidate_scope_snapshot_version")) or 1
    candidate_ids = _string_list(raw_envelope.get("candidate_agent_ids"))

    if candidate_ids:
        selected = _selected_items_for_candidate_ids(
            candidate_ids=candidate_ids,
            selected_agent_set=selected_agent_set,
        )
    elif selected_agent_set is not None:
        selected = selected_agent_set
    else:
        selected = []

    scope = normalize_candidate_scope(
        room_id=room_id,
        source=source,
        group_id=group_id,
        selected_agent_set=selected,
    )
    return scope.model_copy(update={"revision": revision})


def _selected_items_for_candidate_ids(
    *,
    candidate_ids: list[str],
    selected_agent_set: Mapping[str, Any] | Sequence[Any] | None,
) -> list[Any]:
    if selected_agent_set is None:
        return candidate_ids

    registry = _registry_by_agent_id(selected_agent_set)
    return [registry.get(agent_id) or agent_id for agent_id in candidate_ids]


def _registry_by_agent_id(
    selected_agent_set: Mapping[str, Any] | Sequence[Any],
) -> dict[str, Any]:
    registry: dict[str, Any] = {}
    for item in _raw_candidate_items(selected_agent_set):
        agent = _candidate_agent_snapshot(item)
        if agent is not None and agent.agent_id not in registry:
            registry[agent.agent_id] = item
    return registry


def _candidate_agents_from_selected_set(
    selected_agent_set: Mapping[str, Any] | Sequence[Any],
) -> list[CandidateAgentSnapshot]:
    agents: list[CandidateAgentSnapshot] = []
    seen_ids: set[str] = set()
    for item in _raw_candidate_items(selected_agent_set):
        agent = _candidate_agent_snapshot(item)
        if agent is None or agent.agent_id in seen_ids:
            continue
        seen_ids.add(agent.agent_id)
        agents.append(agent)
    return agents


def _raw_candidate_items(
    selected_agent_set: Mapping[str, Any] | Sequence[Any],
) -> list[Any]:
    if isinstance(selected_agent_set, str):
        return [selected_agent_set]

    if isinstance(selected_agent_set, Mapping):
        raw_agents = _first_mapping_value(
            selected_agent_set, "agents", "candidate_agents"
        )
        if isinstance(raw_agents, Sequence) and not isinstance(
            raw_agents, str | bytes
        ):
            items = list(raw_agents)
            if items:
                return items

        raw_ids = _first_mapping_value(
            selected_agent_set, "agent_ids", "candidate_agent_ids"
        )
        if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, str | bytes):
            return list(raw_ids)

        if _looks_like_agent_mapping(selected_agent_set):
            return [selected_agent_set]

        items: list[Any] = []
        for agent_id, value in selected_agent_set.items():
            if not isinstance(agent_id, str):
                continue
            if isinstance(value, str):
                items.append({"agent_id": agent_id, "name": value})
            elif isinstance(value, Mapping):
                items.append({"agent_id": agent_id, **value})
            else:
                items.append(value)
        return items

    return list(selected_agent_set)


def _candidate_agent_snapshot(raw_item: Any) -> CandidateAgentSnapshot | None:
    if isinstance(raw_item, str):
        agent_id = _optional_str(raw_item)
        if agent_id is None:
            return None
        return CandidateAgentSnapshot(agent_id=agent_id)

    if isinstance(raw_item, Mapping):
        agent_id = _optional_str(_first_mapping_value(raw_item, "agent_id", "id"))
        if agent_id is None:
            return None
        return CandidateAgentSnapshot(
            agent_id=agent_id,
            name=_optional_str(_first_mapping_value(raw_item, "name", "agent_name")),
            role=_optional_str(_first_mapping_value(raw_item, "role")),
            capability_summary=_capability_summary_from_mapping(raw_item),
            status=_optional_str(_first_mapping_value(raw_item, "status")),
            source=_optional_str(_first_mapping_value(raw_item, "source")),
        )

    agent_id = _optional_str(_first_attr_value(raw_item, "agent_id", "id"))
    if agent_id is None:
        return None

    agent_card = getattr(raw_item, "agent_card", None)
    name = _optional_str(_first_attr_value(raw_item, "name", "agent_name"))
    role = _optional_str(_first_attr_value(raw_item, "role"))
    capability_summary = _optional_str(
        _first_attr_value(raw_item, "capability_summary")
    )
    if agent_card is not None:
        name = name or _optional_str(getattr(agent_card, "name", None))
        role = role or _optional_str(getattr(agent_card, "role", None))
        capability_summary = capability_summary or _optional_str(
            getattr(agent_card, "capability_summary", None)
        )

    return CandidateAgentSnapshot(
        agent_id=agent_id,
        name=name,
        role=role,
        capability_summary=capability_summary or "",
        status=_optional_str(_first_attr_value(raw_item, "status")),
        source=_optional_str(_first_attr_value(raw_item, "source")),
    )


def _capability_summary_from_mapping(raw_item: Mapping[str, Any]) -> str:
    summary = _optional_str(_first_mapping_value(raw_item, "capability_summary"))
    if summary is not None:
        return summary

    capabilities = _first_mapping_value(raw_item, "capabilities", "skills")
    if isinstance(capabilities, Sequence) and not isinstance(capabilities, str | bytes):
        return ", ".join(
            capability
            for capability in (_optional_str(value) for value in capabilities)
            if capability is not None
        )
    return ""


def _authorization_kind(source: str) -> AuthorizationKind:
    if source == "saved_group":
        return "saved_group_member"
    if source == "room_default":
        return "room_member"
    if source == "mention":
        return "mention"
    return "explicit_selection"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [
        item
        for item in (_optional_str(item) for item in value)
        if item is not None
    ]


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _looks_like_agent_mapping(value: Mapping[str, Any]) -> bool:
    return any(key in value for key in ("agent_id", "id"))


def _first_mapping_value(mapping: Mapping[str, Any], *keys: str) -> Any | None:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _first_attr_value(value: Any, *attrs: str) -> Any | None:
    for attr in attrs:
        if hasattr(value, attr):
            return getattr(value, attr)
    return None
