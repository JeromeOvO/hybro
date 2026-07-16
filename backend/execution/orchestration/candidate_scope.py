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
        return _raw_candidate_items_from_mapping(selected_agent_set)

    if _looks_like_scope_object(selected_agent_set):
        return _raw_candidate_items_from_scope_object(selected_agent_set)

    if not isinstance(selected_agent_set, Sequence):
        return [selected_agent_set]

    return list(selected_agent_set)


def _raw_candidate_items_from_mapping(
    selected_agent_set: Mapping[str, Any],
) -> list[Any]:
    scope_items = _candidate_items_from_agents_and_ids(
        _first_mapping_value(selected_agent_set, "agents", "candidate_agents"),
        _first_mapping_value(
            selected_agent_set, "agent_ids", "candidate_agent_ids"
        ),
    )
    if scope_items is not None:
        return scope_items
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


def _raw_candidate_items_from_scope_object(selected_agent_set: Any) -> list[Any]:
    return (
        _candidate_items_from_agents_and_ids(
            _first_attr_value(selected_agent_set, "agents", "candidate_agents"),
            _first_attr_value(
                selected_agent_set, "agent_ids", "candidate_agent_ids"
            ),
        )
        or []
    )


def _candidate_items_from_agents_and_ids(
    raw_agents: Any,
    raw_ids: Any,
) -> list[Any] | None:
    ids = list(raw_ids) if _is_non_string_sequence(raw_ids) else None
    if _is_non_string_sequence(raw_agents):
        items = list(raw_agents)
        if items:
            return _candidate_items_ordered_by_ids(items, ids) if ids else items
    return ids


def _is_non_string_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def _candidate_items_ordered_by_ids(
    raw_items: Sequence[Any],
    raw_ids: Sequence[Any],
) -> list[Any]:
    items_by_id: dict[str, Any] = {}
    for item in raw_items:
        agent_id = _candidate_item_id(item)
        if agent_id is not None and agent_id not in items_by_id:
            items_by_id[agent_id] = item

    items: list[Any] = []
    seen_ids: set[str] = set()
    for raw_id in raw_ids:
        agent_id = _optional_str(raw_id)
        if agent_id is None or agent_id in seen_ids:
            continue
        seen_ids.add(agent_id)
        items.append(items_by_id.get(agent_id) or agent_id)
    return items


def _candidate_item_id(raw_item: Any) -> str | None:
    if isinstance(raw_item, str):
        return _optional_str(raw_item)
    if isinstance(raw_item, Mapping):
        return _optional_str(_first_mapping_value(raw_item, "agent_id", "id"))
    return _optional_str(_first_attr_value(raw_item, "agent_id", "id"))


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
            status=_status_from_mapping(raw_item),
            source=_optional_str(_first_mapping_value(raw_item, "source")),
        )

    agent_id = _optional_str(_first_attr_value(raw_item, "agent_id", "id"))
    if agent_id is None:
        return None

    agent_card = getattr(raw_item, "agent_card", None)
    name = _optional_str(_first_attr_value(raw_item, "name", "agent_name"))
    role = _optional_str(_first_attr_value(raw_item, "role"))
    capability_summary = _capability_summary_from_object(raw_item)
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
        status=_status_from_object(raw_item),
        source=_optional_str(_first_attr_value(raw_item, "source")),
    )


def _capability_summary_from_mapping(raw_item: Mapping[str, Any]) -> str:
    summary = _optional_str(_first_mapping_value(raw_item, "capability_summary"))
    if summary is not None:
        return summary

    description = _optional_str(_first_mapping_value(raw_item, "description"))
    if description is not None:
        return description

    capabilities = _first_mapping_value(raw_item, "capabilities", "skills")
    if isinstance(capabilities, Sequence) and not isinstance(capabilities, str | bytes):
        return ", ".join(
            capability
            for capability in (_optional_str(value) for value in capabilities)
            if capability is not None
        )
    return ""


def _capability_summary_from_object(raw_item: Any) -> str:
    summary = _optional_str(_first_attr_value(raw_item, "capability_summary"))
    if summary is not None:
        return summary

    description = _optional_str(_first_attr_value(raw_item, "description"))
    if description is not None:
        return description

    capabilities = _first_attr_value(raw_item, "capabilities", "skills")
    if isinstance(capabilities, Sequence) and not isinstance(
        capabilities, str | bytes
    ):
        return ", ".join(
            capability
            for capability in (_optional_str(value) for value in capabilities)
            if capability is not None
        )
    return ""


def _status_from_mapping(raw_item: Mapping[str, Any]) -> str | None:
    status = _optional_str(_first_mapping_value(raw_item, "status", "agent_status"))
    if status is not None:
        return status
    return _status_from_health(_first_mapping_value(raw_item, "is_healthy", "healthy"))


def _status_from_object(raw_item: Any) -> str | None:
    raw_status = _first_attr_value(raw_item, "status", "agent_status")
    status_value = getattr(raw_status, "value", raw_status)
    status = _optional_str(status_value)
    if status is not None:
        return status
    return _status_from_health(_first_attr_value(raw_item, "is_healthy", "healthy"))


def _status_from_health(value: Any) -> str | None:
    if not isinstance(value, bool):
        return None
    return "active" if value else "inactive"


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


def _looks_like_scope_object(value: Any) -> bool:
    return any(
        hasattr(value, attr)
        for attr in (
            "agents",
            "agent_ids",
            "candidate_agents",
            "candidate_agent_ids",
        )
    )


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
