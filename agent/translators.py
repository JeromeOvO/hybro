from __future__ import annotations

from datetime import datetime
from typing import Any

from common.dto.agent import AgentCardSnapshot, AgentInfo, HubAgentDescriptor


def agent_info_from_doc(doc: dict[str, Any]) -> AgentInfo:
    card = _card(doc)
    return AgentInfo(
        agent_id=doc["agent_id"],
        name=card.get("name"),
        description=card.get("description"),
        url=card.get("url"),
        provider_id=doc.get("provider_id"),
        status=_status_value(doc.get("agent_status", "active")),
        capabilities=list(doc.get("capabilities") or _card_capabilities(card)),
        source=doc.get("source", "cloud"),
        hub_id=doc.get("hub_id"),
        is_hub_online=doc.get("is_hub_online"),
        is_public=doc.get("is_public", True),
        public_url=doc.get("public_url"),
        rate_limit_per_user_per_hour=doc.get("rate_limit_per_user_per_hour"),
        rate_limit_system_per_hour=doc.get("rate_limit_system_per_hour"),
        call_count=doc.get("call_count", 0),
        raw_card=dict(card),
    )


def agent_card_from_doc(doc: dict[str, Any]) -> AgentCardSnapshot:
    card = _card(doc)
    return AgentCardSnapshot(
        agent_id=doc["agent_id"],
        name=card.get("name"),
        description=card.get("description"),
        url=card.get("url") or "",
        capabilities=list(doc.get("capabilities") or _card_capabilities(card)),
        raw_card=dict(card),
    )


def registration_doc_from_card(
    *,
    agent_id: str,
    provider_id: str,
    card: AgentCardSnapshot,
    normalized_url: str | None,
    public_url: str | None,
    now: datetime,
    is_public: bool = True,
    rate_limit_per_user_per_hour: int | None = None,
    rate_limit_system_per_hour: int | None = None,
) -> dict[str, Any]:
    raw_card = dict(card.raw_card)
    raw_card.setdefault("name", card.name)
    raw_card.setdefault("description", card.description)
    raw_card.setdefault("url", card.url)
    return {
        "agent_id": agent_id,
        "provider_id": provider_id,
        "agent_card": raw_card,
        "normalized_url": normalized_url,
        "public_url": public_url,
        "agent_status": "active",
        "is_public": is_public,
        "source": "cloud",
        "capabilities": list(card.capabilities),
        "rate_limit_per_user_per_hour": rate_limit_per_user_per_hour,
        "rate_limit_system_per_hour": rate_limit_system_per_hour,
        "call_count": 0,
        "created_at": now,
        "updated_at": now,
    }


def hub_descriptor_to_doc(
    *,
    hub_id: str,
    owner_user_id: str,
    descriptor: HubAgentDescriptor,
    agent_id: str,
    normalized_url: str | None,
    public_url: str | None,
) -> dict[str, Any]:
    card = dict(descriptor.raw_card or {})
    if descriptor.name is not None:
        card["name"] = descriptor.name
    if descriptor.url is not None:
        card["url"] = descriptor.url
    return {
        "agent_id": agent_id,
        "provider_id": owner_user_id,
        "source": "hub",
        "hub_id": hub_id,
        "local_agent_id": descriptor.agent_id,
        "agent_status": "active",
        "is_public": False,
        "normalized_url": normalized_url,
        "public_url": public_url,
        "capabilities": list(descriptor.capabilities),
        "agent_card": card,
    }


def docs_by_vector_order(
    docs: list[dict[str, Any]],
    agent_ids: list[str],
) -> list[dict[str, Any]]:
    docs_by_id = {doc.get("agent_id"): doc for doc in docs}
    return [docs_by_id[agent_id] for agent_id in agent_ids if agent_id in docs_by_id]


def _card(doc: dict[str, Any]) -> dict[str, Any]:
    return dict(doc.get("agent_card") or {})


def _card_capabilities(card: dict[str, Any]) -> list[str]:
    skills = card.get("skills")
    if not isinstance(skills, list):
        return []
    return [
        str(skill.get("name") or skill.get("id"))
        for skill in skills
        if isinstance(skill, dict) and (skill.get("name") or skill.get("id"))
    ]


def _status_value(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value) if value is not None else "active"
