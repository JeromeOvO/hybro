from __future__ import annotations

import uuid

from common.protocols import (
    AgentRepository,
    MessageRepository,
    MongoDAL,
    RoomRepository,
)
from common.utils.logger import get_logger
from models.agent import Agent
from models.agent_group import AgentGroup
from models.room import MessageContent, Room, RoomAgentMessage, RoomUserMessage

logger = get_logger(__name__)


class AppShellRepositoryStore:
    """PR2 compatibility store for low-risk app-shell DB consumers.

    This intentionally exposes only the methods used by room membership,
    debate injection, and room coordination. Later migration PRs should extend
    this store only when their first migrated consumer needs a method.
    """

    def __init__(
        self,
        *,
        mongo: MongoDAL,
        room_repository: RoomRepository,
        message_repository: MessageRepository,
        agent_repository: AgentRepository,
    ) -> None:
        self._agent_groups = mongo.collection("agent_groups")
        self._room_repository = room_repository
        self._message_repository = message_repository
        self._agent_repository = agent_repository

    async def get_agent_group_by_id(self, group_id: str) -> AgentGroup | None:
        try:
            return _safe_parse_agent_group(
                await self._agent_groups.find_one({"group_id": group_id})
            )
        except Exception:
            logger.error("Failed to get agent group", exc_info=True)
            return None

    async def get_all_active_agents(self, user_id: str | None = None) -> list[Agent]:
        try:
            docs = await self._agent_repository.list_visible(
                user_id=user_id,
                active_only=True,
                limit=0,
            )
        except Exception:
            logger.error("Failed to get active agents", exc_info=True)
            return []
        agents = [_safe_parse_agent(doc) for doc in docs if doc is not None]
        return [agent for agent in agents if agent is not None]

    async def get_agent_name_by_agent_id(self, agent_id: str) -> str | None:
        try:
            doc = await self._agent_repository.get_by_id(agent_id)
        except Exception:
            logger.error("Failed to get agent name", exc_info=True)
            return None
        card = (doc or {}).get("agent_card") or {}
        return card.get("name") if isinstance(card, dict) else getattr(card, "name", None)

    async def get_room_by_room_id(self, room_id: str) -> Room | None:
        try:
            return _safe_parse_room(await self._room_repository.get_by_id(room_id))
        except Exception:
            logger.error("Failed to get room", exc_info=True)
            return None

    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> RoomUserMessage | None:
        try:
            return _safe_parse_user_message(
                await self._message_repository.get_user_message_by_id(message_id)
            )
        except Exception:
            logger.error("Failed to get room user message", exc_info=True)
            return None

    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RoomAgentMessage | None:
        try:
            return _safe_parse_agent_message(
                await self._message_repository.get_agent_message_by_id(message_id)
            )
        except Exception:
            logger.error("Failed to get room agent message", exc_info=True)
            return None

    async def get_room_agent_messages_by_related_message_id(
        self, related_message_id: str
    ) -> list[RoomAgentMessage]:
        try:
            docs = await self._message_repository.get_agent_messages_by_related_message_id(
                related_message_id
            )
        except Exception:
            logger.error("Failed to get related agent messages", exc_info=True)
            return []
        messages = [_safe_parse_agent_message(doc) for doc in docs if doc is not None]
        return [message for message in messages if message is not None]

    async def add_room_agent_message(self, room_agent_message: RoomAgentMessage) -> bool:
        try:
            if room_agent_message.message_id == "":
                room_agent_message.message_id = str(uuid.uuid4())
            await self._message_repository.save_agent_message(
                room_agent_message.model_dump(mode="json")
            )
            return True
        except Exception:
            logger.error("Failed to add room agent message", exc_info=True)
            return False

    async def update_room_agent_message_with_new_message_content_by_message_id(
        self, message_id: str, message_content: MessageContent
    ) -> bool:
        try:
            return await self._message_repository.update_agent_message(
                message_id,
                {"message_content": message_content.model_dump(mode="json")},
            )
        except Exception:
            logger.error("Failed to update room agent message content", exc_info=True)
            return False


def _safe_parse_agent_group(doc: dict | None) -> AgentGroup | None:
    if doc is None:
        return None
    try:
        return AgentGroup.model_validate(doc)
    except Exception:
        logger.warning("Invalid agent group document", exc_info=True)
        return None


def _safe_parse_agent(doc: dict | None) -> Agent | None:
    if doc is None:
        return None
    try:
        return Agent.model_validate(doc)
    except Exception:
        logger.warning("Invalid agent document", exc_info=True)
        return None


def _safe_parse_room(doc: dict | None) -> Room | None:
    if doc is None:
        return None
    try:
        return Room.model_validate(doc)
    except Exception:
        logger.warning("Invalid room document", exc_info=True)
        return None


def _safe_parse_user_message(doc: dict | None) -> RoomUserMessage | None:
    if doc is None:
        return None
    try:
        return RoomUserMessage.model_validate(doc)
    except Exception:
        logger.warning("Invalid room user message document", exc_info=True)
        return None


def _safe_parse_agent_message(doc: dict | None) -> RoomAgentMessage | None:
    if doc is None:
        return None
    try:
        return RoomAgentMessage.model_validate(doc)
    except Exception:
        logger.warning("Invalid room agent message document", exc_info=True)
        return None
