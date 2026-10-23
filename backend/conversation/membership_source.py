from __future__ import annotations

from typing import Any

from common.dto import AgentInfo, SavedAgentGroupSnapshot
from common.utils.logger import get_logger
from conversation.unbound import UNBOUND_RUNTIME_STORE

logger = get_logger(__name__)


class RepositoryRoomMembershipSeedSource:
    def __init__(
        self,
        *,
        membership_store=None,
        agent_registry=None,
    ) -> None:
        self._store = membership_store or UNBOUND_RUNTIME_STORE
        self._agent_registry = agent_registry

    def bind_store(self, membership_store) -> None:
        self._store = membership_store

    async def get_saved_group(self, group_id: str) -> SavedAgentGroupSnapshot | None:
        group = await self._store.get_agent_group_by_id(group_id)
        if group is None:
            return None
        return SavedAgentGroupSnapshot(
            group_id=group.group_id,
            name=group.name,
            owner_id=group.owner_id,
            type=group.type,
            agent_ids=list(group.agents or []),
        )

    async def list_current_agents(self, user_id: str | None) -> list[AgentInfo]:
        if self._agent_registry is not None:
            try:
                return await self._agent_registry.list_visible_agents(
                    user_id=user_id, active_only=True
                )
            except Exception as exc:
                logger.debug(
                    "Room membership source falling back to database active agents: %s",
                    exc,
                )

        agents = await self._store.get_all_active_agents(user_id=user_id)
        return [self.agent_info_from_record(agent) for agent in agents or []]

    @staticmethod
    def agent_info_from_record(agent: Any) -> AgentInfo:
        return _agent_info_from_record(agent)


def _agent_info_from_record(agent: Any) -> AgentInfo:
    card = getattr(agent, "agent_card", None)
    raw_status = getattr(agent, "agent_status", None)
    status = getattr(raw_status, "value", None)
    agent_id = getattr(agent, "agent_id", None)
    name = getattr(card, "name", None)
    resolved_status = status or str(raw_status or "active")
    if not agent_id or name is None or raw_status is None or not resolved_status:
        logger.warning(
            "Room membership agent missing critical fields: agent_id=%r name=%r status=%r",
            agent_id,
            name,
            resolved_status,
        )
    return AgentInfo(
        agent_id=agent_id,
        name=name,
        description=getattr(card, "description", None),
        url=getattr(card, "url", None),
        provider_id=getattr(agent, "provider_id", None),
        status=resolved_status,
        capabilities=[],
        source=getattr(agent, "source", "cloud"),
        is_public=getattr(agent, "is_public", True),
        public_url=getattr(agent, "public_url", None),
        raw_card=card.model_dump(mode="json") if hasattr(card, "model_dump") else {},
    )


__all__ = ["RepositoryRoomMembershipSeedSource"]
