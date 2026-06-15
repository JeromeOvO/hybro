from __future__ import annotations

import uuid

from app_shell.repository_parts.parsing import (
    _safe_parse_chat_context,
    _safe_parse_room_memory,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.memory import ChatContext, RoomMemory

logger = get_logger(__name__)


class AppShellMemoryStore:
    def __init__(
        self,
        *,
        chat_contexts,
        user_memories,
        agent_memories,
        room_memories,
        room_repository,
    ) -> None:
        self._chat_contexts = chat_contexts
        self._user_memories = user_memories
        self._agent_memories = agent_memories
        self._room_memories = room_memories
        self._room_repository = room_repository

    async def get_room_memory_by_room_id(self, room_id: str) -> RoomMemory | None:
        try:
            return _safe_parse_room_memory(
                await self._room_memories.find_one({"room_id": room_id})
            )
        except Exception:
            logger.error("Failed to get room memory", exc_info=True)
            return None

    async def add_chat_context(self, chat_context: ChatContext) -> bool:
        try:
            if chat_context.memory_id == "":
                chat_context.memory_id = str(uuid.uuid4())
            await self._chat_contexts.insert_one(chat_context.model_dump(mode="json"))
            return True
        except Exception:
            logger.error("Failed to add chat context", exc_info=True)
            return False

    async def get_chat_context_by_session_id(
        self, session_id: str
    ) -> ChatContext | None:
        try:
            return _safe_parse_chat_context(
                await self._chat_contexts.find_one({"session_id": session_id})
            )
        except Exception:
            logger.error("Failed to get chat context", exc_info=True)
            return None

    async def update_chat_context_by_session_id(
        self, session_id: str, chat_context: ChatContext
    ) -> bool:
        try:
            await self._chat_contexts.update_one(
                {"session_id": session_id},
                {"$set": chat_context.model_dump(exclude_unset=True, mode="json")},
            )
            return True
        except Exception:
            logger.error("Failed to update chat context", exc_info=True)
            return False

    async def delete_chat_context_by_session_id(self, session_id: str) -> bool:
        try:
            await self._chat_contexts.delete_one({"session_id": session_id})
            return True
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

    async def update_turn_notes(
        self, room_id: str, turn_id: str, turn_notes: dict
    ) -> bool:
        updater = getattr(self._room_repository, "update_turn_notes", None)
        if callable(updater):
            try:
                return await updater(room_id, turn_id, turn_notes)
            except Exception:
                logger.error("Failed to update turn notes", exc_info=True)
        return False
