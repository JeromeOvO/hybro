from __future__ import annotations

from typing import Any

from app_shell.repository_parts.parsing import (
    _safe_parse_agent,
    _safe_parse_agent_group,
    _safe_parse_room,
)
from common.utils.logger import get_logger
from models.agent import Agent
from models.agent_group import AgentGroup
from models.room import Room

logger = get_logger(__name__)


class AppShellAgentRoomStore:
    def __init__(
        self, *, agent_groups, agents, room_repository, agent_repository
    ) -> None:
        self._agent_groups = agent_groups
        self._agents = agents
        self._room_repository = room_repository
        self._agent_repository = agent_repository

    async def add_agent_group(self, agent_group: AgentGroup) -> bool:
        try:
            await self._agent_groups.insert_one(agent_group.model_dump(mode="json"))
            return True
        except Exception:
            logger.error("Failed to add agent group", exc_info=True)
            return False

    async def get_agent_groups_by_owner(self, owner_id: str) -> list[AgentGroup]:
        try:
            docs = await self._agent_groups.find({"owner_id": owner_id})
            groups = [_safe_parse_agent_group(doc) for doc in docs if doc is not None]
            return [group for group in groups if group is not None]
        except Exception:
            logger.error("Failed to get agent groups by owner", exc_info=True)
            return []

    async def get_agent_group_by_id(self, group_id: str) -> AgentGroup | None:
        try:
            return _safe_parse_agent_group(
                await self._agent_groups.find_one({"group_id": group_id})
            )
        except Exception:
            logger.error("Failed to get agent group", exc_info=True)
            return None

    async def update_agent_group(self, group_id: str, updates: dict) -> bool:
        try:
            return await self._agent_groups.update_one(
                {"group_id": group_id},
                {"$set": dict(updates)},
            )
        except Exception:
            logger.error("Failed to update agent group", exc_info=True)
            return False

    async def delete_agent_group(self, group_id: str) -> bool:
        try:
            return await self._agent_groups.delete_one({"group_id": group_id})
        except Exception:
            logger.error("Failed to delete agent group", exc_info=True)
            return False

    async def get_all_active_agents(self, user_id: str | None = None) -> list[Agent]:
        try:
            docs = await self._agent_repository.list_visible(
                user_id=user_id,
                active_only=True,
                limit=0,
            )
            agents = [_safe_parse_agent(doc) for doc in docs if doc is not None]
            return [agent for agent in agents if agent is not None]
        except Exception:
            logger.error("Failed to get active agents", exc_info=True)
            return []

    async def get_agent_name_by_agent_id(self, agent_id: str) -> str | None:
        try:
            doc = await self._agent_repository.get_by_id(agent_id)
        except Exception:
            logger.error("Failed to get agent name", exc_info=True)
            return None
        card = (doc or {}).get("agent_card") or {}
        return (
            card.get("name") if isinstance(card, dict) else getattr(card, "name", None)
        )

    async def get_agent_by_agent_id(self, agent_id: str) -> Agent | None:
        try:
            return _safe_parse_agent(await self._agent_repository.get_by_id(agent_id))
        except Exception:
            logger.error("Failed to get agent", exc_info=True)
            return None

    async def get_agents_with_conditions(
        self,
        query: dict[str, Any] | None = None,
        limit: int = 0,
    ) -> list[Agent]:
        try:
            # Legacy room selection accepts arbitrary agent predicates; use the
            # DAL collection directly to preserve that query surface during
            # migration instead of applying AgentRepository visibility filters.
            docs = await self._agents.find(dict(query or {}), limit=limit or None)
            agents = [_safe_parse_agent(doc) for doc in docs if doc is not None]
            return [agent for agent in agents if agent is not None]
        except Exception:
            logger.error("Failed to get agents with conditions", exc_info=True)
            return []

    async def increment_agent_call_count(self, agent_id: str, *, success: bool) -> None:
        try:
            await self._agent_repository.increment_agent_call_count(
                agent_id,
                success=success,
            )
        except Exception:
            logger.error("Failed to increment agent call count", exc_info=True)

    async def get_room_by_room_id(self, room_id: str) -> Room | None:
        try:
            return _safe_parse_room(await self._room_repository.get_by_id(room_id))
        except Exception:
            logger.error("Failed to get room", exc_info=True)
            return None

    async def get_rooms_by_room_owner_id(self, room_owner_id: str) -> list[Room]:
        try:
            docs = await self._room_repository.get_by_owner(room_owner_id)
            rooms = [_safe_parse_room(doc) for doc in docs if doc is not None]
            return [room for room in rooms if room is not None]
        except Exception:
            logger.error("Failed to get rooms by owner", exc_info=True)
            return []

    async def update_room_by_room_id(self, room_id: str, room: Room) -> bool:
        try:
            updates = room.model_dump(exclude_unset=True, mode="json")
            updated = await self._room_repository.update(room_id, updates)
            if updated:
                return True
            # MongoDAL update_one returns False for matched no-op writes because
            # it only exposes modified/upserted state. Legacy database_service
            # treated any matched room update as success, so confirm existence
            # before reporting failure.
            return await self._room_repository.get_by_id(room_id) is not None
        except Exception:
            logger.error("Failed to update room", exc_info=True)
            return False
