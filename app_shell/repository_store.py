from __future__ import annotations

import uuid

from common.protocols import (
    AgentRepository,
    MessageRepository,
    MongoDAL,
    RoomRepository,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.agent import Agent
from models.agent_group import AgentGroup
from models.memory import ChatContext
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
        self._chat_contexts = mongo.collection("chat_contexts")
        self._user_memories = mongo.collection("user_memories")
        self._agent_memories = mongo.collection("agent_memories")
        self._room_agent_messages = mongo.collection("room_agent_messages")
        self._room_repository = room_repository
        self._message_repository = message_repository
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
        except Exception:
            logger.error("Failed to get agent groups by owner", exc_info=True)
            return []
        groups = [_safe_parse_agent_group(doc) for doc in docs if doc is not None]
        return [group for group in groups if group is not None]

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

    async def get_task_messages_for_room(
        self, room_id: str, *, limit: int = 50
    ) -> list[RoomAgentMessage]:
        try:
            docs = await self._message_repository.get_task_messages_for_room(
                room_id,
                limit,
            )
        except Exception:
            logger.error("Failed to get task messages for room", exc_info=True)
            return []
        messages = [_safe_parse_agent_message(doc) for doc in docs if doc is not None]
        return [message for message in messages if message is not None]

    async def get_pending_task_messages_for_user(
        self, user_id: str, states: list[str]
    ) -> list[RoomAgentMessage]:
        try:
            docs = await self._message_repository.get_pending_task_messages_for_user(
                user_id,
                states,
            )
        except Exception:
            logger.error("Failed to get pending task messages", exc_info=True)
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

    async def update_room_agent_message_by_message_id(
        self, message_id: str, room_agent_message: RoomAgentMessage
    ) -> bool:
        try:
            update_data = _agent_message_update_payload(room_agent_message)
            return await self._message_repository.update_agent_message(
                message_id,
                update_data,
            )
        except Exception:
            logger.error("Failed to update room agent message", exc_info=True)
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

    async def update_last_notified_state(self, message_id: str, state: str) -> bool:
        try:
            return await self._room_agent_messages.update_one(
                {"message_id": message_id, "last_notified_state": {"$ne": state}},
                {"$set": {"last_notified_state": state}},
            )
        except Exception:
            logger.error("Failed to update last notified state", exc_info=True)
            return False

    async def resolve_client_request_id_for_agent_message(
        self,
        room_agent_message: RoomAgentMessage,
    ) -> str | None:
        if room_agent_message.client_request_id:
            return room_agent_message.client_request_id
        visited: set[str] = set()
        cursor = room_agent_message.related_message_id
        for _ in range(12):
            if not cursor or cursor in visited:
                break
            visited.add(cursor)

            user_message = await self.get_room_user_message_by_message_id(cursor)
            if user_message and user_message.client_request_id:
                return user_message.client_request_id

            parent_agent = await self.get_room_agent_message_by_message_id(cursor)
            if parent_agent is None:
                break
            if parent_agent.client_request_id:
                return parent_agent.client_request_id
            cursor = parent_agent.related_message_id

        if room_agent_message.turn_id:
            turn_user_message = await self.get_room_user_message_by_message_id(
                room_agent_message.turn_id
            )
            if turn_user_message and turn_user_message.client_request_id:
                return turn_user_message.client_request_id

        return None

    async def add_chat_context(self, chat_context: ChatContext) -> bool:
        try:
            await self._chat_contexts.insert_one(chat_context.model_dump(mode="json"))
            return True
        except Exception:
            logger.error("Failed to add chat context", exc_info=True)
            return False

    async def get_chat_context_by_session_id(
        self,
        session_id: str,
    ) -> ChatContext | None:
        try:
            return _safe_parse_chat_context(
                await self._chat_contexts.find_one({"session_id": session_id})
            )
        except Exception:
            logger.error("Failed to get chat context", exc_info=True)
            return None

    async def update_chat_context_by_session_id(
        self,
        session_id: str,
        chat_context: ChatContext,
    ) -> bool:
        try:
            return await self._chat_contexts.update_one(
                {"session_id": session_id},
                {"$set": chat_context.model_dump(mode="json")},
            )
        except Exception:
            logger.error("Failed to update chat context", exc_info=True)
            return False

    async def delete_chat_context_by_session_id(self, session_id: str) -> bool:
        try:
            return await self._chat_contexts.delete_one({"session_id": session_id})
        except Exception:
            logger.error("Failed to delete chat context", exc_info=True)
            return False

    async def increment_user_interactions(self, user_id: str) -> bool:
        now = utcnow()
        try:
            return await self._user_memories.update_one(
                {"user_id": user_id},
                {
                    "$inc": {"total_interactions": 1},
                    "$set": {"last_active_at": now},
                    "$setOnInsert": {"user_id": user_id, "created_at": now},
                },
                upsert=True,
            )
        except Exception:
            logger.error("Failed to increment user interactions", exc_info=True)
            return False

    async def record_agent_call(
        self,
        *,
        agent_id: str,
        success: bool,
        response_time_ms: float = 0.0,
    ) -> bool:
        inc_fields: dict[str, float | int] = {
            "total_calls": 1,
            "total_response_time_ms": response_time_ms,
        }
        if success:
            inc_fields["successful_calls"] = 1
        try:
            return await self._agent_memories.update_one(
                {"agent_id": agent_id},
                {
                    "$inc": inc_fields,
                    "$set": {"last_called_at": utcnow()},
                    "$setOnInsert": {"agent_id": agent_id},
                },
                upsert=True,
            )
        except Exception:
            logger.error("Failed to record agent call", exc_info=True)
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


def _agent_message_update_payload(room_agent_message: RoomAgentMessage) -> dict:
    update_data = room_agent_message.model_dump(mode="json")
    task_tracking_fields = {
        "webhook_token_hash",
        "pending_continuation",
        "last_notified_state",
        "agent_url",
        "task_created_at",
        "task_updated_at",
        "task_content",
        "has_task_tracking",
    }
    for field in task_tracking_fields:
        if update_data.get(field) is None:
            update_data.pop(field, None)
    return update_data


def _safe_parse_chat_context(doc: dict | None) -> ChatContext | None:
    if doc is None:
        return None
    try:
        return ChatContext.model_validate(doc)
    except Exception:
        logger.warning("Invalid chat context document", exc_info=True)
        return None
