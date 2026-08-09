from __future__ import annotations

from common.utils.logger import get_logger
from dal.runtime_store.parts.parsing import _safe_parse_room_memory
from models.memory import RoomMemory

logger = get_logger(__name__)


class MemoryRuntimeStorePart:
    def __init__(
        self,
        *,
        room_memories,
        room_repository,
    ) -> None:
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
