"""Chat product reads and writes over the conversation container's rooms."""

from __future__ import annotations

from common.dto import CreateRoomRequest, RoomInfo
from common.protocols import RoomRepository
from conversation import ConversationFacade, room_info_from_doc


class ChatFacade:
    def __init__(
        self,
        *,
        conversation: ConversationFacade,
        repository: RoomRepository,
    ) -> None:
        self._conversation = conversation
        self._repository = repository

    async def get_room(self, room_id: str) -> RoomInfo | None:
        return await self._conversation.get_room(room_id)

    async def create_room(self, request: CreateRoomRequest) -> RoomInfo:
        return await self._conversation.create_room(request)

    async def update_room(self, room_id: str, updates: dict) -> RoomInfo | None:
        return await self._conversation.update_room(room_id, updates)

    async def update_room_default_mode(
        self,
        room_id: str,
        *,
        use_supervisor: bool,
    ) -> bool:
        """Atomically update only the persisted room-mode flag."""
        updated = await self._repository.update_fields(
            room_id,
            {"extend_info.use_supervisor": use_supervisor},
        )
        return updated is not None

    async def list_rooms_for_owner(self, owner_id: str) -> list[RoomInfo]:
        return [
            room_info_from_doc(doc)
            for doc in await self._repository.get_by_owner(owner_id)
        ]

    async def list_room_history_for_owner(
        self, owner_id: str, *, limit: int = 100
    ) -> list[RoomInfo]:
        return [
            room_info_from_doc(doc)
            for doc in await self._repository.get_history_by_owner(
                owner_id, limit=limit
            )
        ]


__all__ = ["ChatFacade"]
