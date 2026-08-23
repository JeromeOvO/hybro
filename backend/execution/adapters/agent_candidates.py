"""Production AgentToolCandidateSource over the agent registry."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from common.protocols import AgentExclusionReader, AgentRegistry
from execution.orchestrator.a2a_runtime.models import AgentToolCandidate


def _digest_json(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256(canonical.encode()).hexdigest()


def _card_digest(raw_card: dict[str, Any]) -> str:
    return _digest_json(raw_card or {})


def _endpoint_scope_digest(endpoint_scope: str) -> str:
    return sha256(endpoint_scope.encode()).hexdigest()


def _mode_list(raw_card: dict[str, Any], *keys: str, default: list[str]) -> list[str]:
    for key in keys:
        value = raw_card.get(key)
        if isinstance(value, list):
            return [str(item) for item in value]
    return list(default)


def _direct_capabilities(info: Any, raw_card: dict[str, Any]) -> list[str]:
    capabilities = {"sync", "poll"}
    declared = raw_card.get("capabilities")
    if isinstance(declared, dict):
        if declared.get("streaming") or declared.get("stream"):
            capabilities.add("stream")
        if declared.get("pushNotifications") or declared.get("push_notifications"):
            capabilities.add("poll")
    for capability in getattr(info, "capabilities", None) or []:
        name = str(getattr(capability, "value", capability))
        if name in {"streaming", "stream", "message/stream"}:
            capabilities.add("stream")
        if name in {"push_notifications", "pushNotifications", "push-notifications"}:
            capabilities.add("poll")
    return sorted(capabilities)


def _skill_items(raw_card: dict[str, Any]) -> list[dict[str, Any]]:
    skills = raw_card.get("skills")
    if not isinstance(skills, list) or not skills:
        return []
    items: list[dict[str, Any]] = []
    for skill in skills:
        if isinstance(skill, dict):
            items.append(skill)
        elif hasattr(skill, "model_dump"):
            items.append(skill.model_dump(mode="json"))
    return items


class AgentServiceCandidateSource:
    """List in-scope agent candidates from the active/visible agent registry."""

    def __init__(
        self,
        *,
        agents: AgentRegistry,
        exclusion_reader: AgentExclusionReader | None = None,
    ) -> None:
        self._agents = agents
        self._exclusion_reader = exclusion_reader

    async def list_candidates(
        self,
        *,
        run_id: str,
        room_id: str,
        room_epoch: int,
        requesting_subject_id: str,
        candidate_agent_ids: list[str],
    ) -> list[AgentToolCandidate]:
        del run_id, room_id, room_epoch
        excluded = (
            await self._exclusion_reader.get_excluded_agent_ids()
            if self._exclusion_reader is not None
            else frozenset()
        )
        infos = await self._agents.get_agents_by_ids(list(candidate_agent_ids))
        candidates: list[AgentToolCandidate] = []
        seen: set[tuple[str, str | None]] = set()
        for info in infos:
            if not info or not info.agent_id:
                continue
            raw_card = dict(getattr(info, "raw_card", None) or {})
            active = str(getattr(info, "status", "") or "") == "active"
            authorized = bool(
                getattr(info, "is_public", True)
                or getattr(info, "provider_id", None) == requesting_subject_id
            )
            transport_kind = "direct"
            endpoint_scope = str(getattr(info, "url", None) or "")
            display_name = str(
                raw_card.get("name") or getattr(info, "name", None) or info.agent_id
            )
            description = str(
                raw_card.get("description") or getattr(info, "description", None) or ""
            )
            card_digest = _card_digest(raw_card)
            scope_digest = (
                _endpoint_scope_digest(endpoint_scope) if endpoint_scope else ""
            )
            input_modes = _mode_list(
                raw_card,
                "defaultInputModes",
                "default_input_modes",
                "input_modes",
                default=["text"],
            )
            output_modes = _mode_list(
                raw_card,
                "defaultOutputModes",
                "default_output_modes",
                "output_modes",
                default=[],
            )
            capabilities = _direct_capabilities(info, raw_card)

            for skill in _skill_items(raw_card):
                skill_id = str(skill.get("id") or skill.get("name") or "")
                identity = (info.agent_id, skill_id)
                if not skill_id or identity in seen:
                    continue
                seen.add(identity)
                candidates.append(
                    AgentToolCandidate(
                        agent_id=info.agent_id,
                        skill_id=skill_id,
                        display_name=(
                            f"{display_name} - {skill.get('name') or skill_id}"
                        ).strip()[:120],
                        description=str(
                            skill.get("description") or description or ""
                        ).strip()[:500],
                        card_digest=card_digest,
                        endpoint_scope=endpoint_scope,
                        endpoint_scope_digest=scope_digest,
                        transport_kind=transport_kind,
                        direct_capabilities=capabilities,
                        active=active,
                        authorized=authorized,
                        excluded=info.agent_id in excluded,
                        input_modes=input_modes,
                        output_modes=output_modes,
                    )
                )
            # An agent without skills (or with unusable skill entries) still
            # exposes one whole-agent tool.
            identity = (info.agent_id, None)
            if identity not in seen:
                seen.add(identity)
                candidates.append(
                    AgentToolCandidate(
                        agent_id=info.agent_id,
                        skill_id=None,
                        display_name=display_name.strip()[:120] or "Agent",
                        description=description.strip()[:500],
                        card_digest=card_digest,
                        endpoint_scope=endpoint_scope,
                        endpoint_scope_digest=scope_digest,
                        transport_kind=transport_kind,
                        direct_capabilities=capabilities,
                        active=active,
                        authorized=authorized,
                        excluded=info.agent_id in excluded,
                        input_modes=input_modes,
                        output_modes=output_modes,
                    )
                )
        return candidates


__all__ = [
    "AgentServiceCandidateSource",
    "_card_digest",
    "_endpoint_scope_digest",
]
