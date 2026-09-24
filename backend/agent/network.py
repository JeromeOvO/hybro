from __future__ import annotations

from pydantic import JsonValue

from agent.protocols import AgentGroupStoreCompatibility
from common.dto import AgentInfo
from common.errors import AppError, ConflictError, NotFoundError, ValidationError
from common.protocols import AgentRegistry
from models.agent_group import BUILTIN_GROUP_ALL_AGENTS, BUILTIN_GROUP_ROOM_TEAM
from models.agent_network import AgentDiscoveryResponse, DiscoveredAgent


class AgentNetworkService:
    def __init__(
        self,
        registry: AgentRegistry,
        groups: AgentGroupStoreCompatibility,
        *,
        requesting_user_id: str,
    ) -> None:
        self._registry = registry
        self._groups = groups
        self._requesting_user_id = requesting_user_id

    async def discover(self, group_id: str | None = None) -> AgentDiscoveryResponse:
        agent_ids = await self._group_agent_ids(group_id)
        agents = await self._registry.list_visible_agents(
            user_id=self._requesting_user_id,
            active_only=True,
            query={"agent_id": {"$in": agent_ids}} if agent_ids is not None else None,
            exhaust=True,
        )
        return AgentDiscoveryResponse(
            agents=[
                DiscoveredAgent(
                    agent_id=agent.agent_id,
                    agent_card=_discovery_card(agent.raw_card),
                )
                for agent in agents
            ]
        )

    async def resolve_target(
        self, agent_id: str, group_id: str | None = None
    ) -> AgentInfo:
        agent_ids = await self._group_agent_ids(group_id)
        agents = await self._registry.list_visible_agents(
            user_id=self._requesting_user_id,
            query={"agent_id": agent_id},
        )
        if not agents:
            raise NotFoundError("Agent", agent_id)
        agent = agents[0]
        if agent.status != "active":
            raise ConflictError(
                "Agent is not active",
                details={"agent_id": agent_id, "status": agent.status},
            )
        if agent_ids is not None and agent_id not in agent_ids:
            raise ConflictError(
                "Agent is not a member of the selected group",
                details={"agent_id": agent_id, "group_id": group_id},
            )
        return agent

    async def _group_agent_ids(self, group_id: str | None) -> list[str] | None:
        if group_id is None or group_id == BUILTIN_GROUP_ALL_AGENTS:
            return None
        if group_id == BUILTIN_GROUP_ROOM_TEAM:
            raise ValidationError(
                "room_team requires a room and cannot scope the agent network",
                details={"group_id": group_id},
            )
        group = await self._groups.get_agent_group_by_id(group_id)
        if group is None:
            raise NotFoundError("AgentGroup", group_id)
        if group.owner_id != self._requesting_user_id:
            raise AppError(
                "Cannot access another owner's agent group", code="FORBIDDEN"
            )
        return group.agents


def _discovery_card(raw_card: dict[str, JsonValue]) -> dict[str, JsonValue]:
    card = dict(raw_card)
    authentication = card.get("authentication")
    if isinstance(authentication, dict) and "credentials" in authentication:
        card["authentication"] = {
            key: value for key, value in authentication.items() if key != "credentials"
        }
    return card
