"""Room document reads and writes for the conversation module."""

from __future__ import annotations

from common.utils.logger import get_logger
from conversation.repository.message_documents import _safe_parse_room
from models.room import Room

logger = get_logger(__name__)


class ConversationRoomStore:
    def __init__(self, *, room_repository) -> None:
        self._room_repository = room_repository

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
            # Lifecycle fencing is owned by RoomFiles. A Room model hydrated
            # before an artifact write contains a stale write_leases snapshot;
            # writing that snapshot back can erase an active lease between file
            # content persistence and finalization. Legacy room updates may not
            # mutate deletion/fencing state.
            for protected_field in (
                "write_leases",
                "lifecycle_state",
                "deletion_id",
                "deletion_started_at",
                "deletion_phase",
            ):
                updates.pop(protected_field, None)
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
