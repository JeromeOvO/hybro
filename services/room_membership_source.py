from __future__ import annotations

from typing import Any

from common.dto import AgentInfo, SavedAgentGroupSnapshot
from models.request import AgentCenterRequest
from services.agent_service import agent_service
from services.database_service import db_service


class LegacyRoomMembershipSeedSource:
    def __init__(
        self,
        *,
        database_service=db_service,
        agent_service_adapter=agent_service,
    ) -> None:
        self._database_service = database_service
        self._agent_service = agent_service_adapter

    async def get_saved_group(self, group_id: str) -> SavedAgentGroupSnapshot | None:
        group = await self._database_service.get_agent_group_by_id(group_id)
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
        try:
            response = await self._agent_service.get_agents_with_conditions(
                AgentCenterRequest(
                    user_id=user_id,
                    query={"agent_status": "active"},
                    limit=0,
                )
            )
            if response.success and response.agents is not None:
                return [_agent_info_from_legacy(agent) for agent in response.agents]
        except Exception:
            pass

        agents = await self._database_service.get_all_active_agents(user_id=user_id)
        return [_agent_info_from_legacy(agent) for agent in agents or []]


def _agent_info_from_legacy(agent: Any) -> AgentInfo:
    card = getattr(agent, "agent_card", None)
    status = getattr(getattr(agent, "agent_status", None), "value", None)
    return AgentInfo(
        agent_id=agent.agent_id,
        name=getattr(card, "name", None),
        description=getattr(card, "description", None),
        url=getattr(card, "url", None),
        provider_id=getattr(agent, "provider_id", None),
        status=status or str(getattr(agent, "agent_status", "active")),
        capabilities=[],
        source=getattr(agent, "source", "cloud"),
        hub_id=getattr(agent, "hub_id", None),
        is_public=getattr(agent, "is_public", True),
        public_url=getattr(agent, "public_url", None),
        raw_card=card.model_dump(mode="json") if hasattr(card, "model_dump") else {},
    )


__all__ = ["LegacyRoomMembershipSeedSource"]
