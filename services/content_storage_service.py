"""Compatibility shim for the legacy content storage service import path."""

from typing import Protocol

from models.compaction import ContentReference
from platform_module.content_storage import ContentExpiredError, hash_content


class ContentStorageDelegate(Protocol):
    async def upsert_full_content(
        self,
        room_id: str,
        turn_id: str,
        content: str,
        content_type: str,
        turn_notes: dict | None = None,
    ) -> str: ...
    async def get_content_by_document_id(self, document_id: str) -> str | None: ...
    async def get_content_by_turn_id(self, room_id: str, turn_id: str) -> str | None: ...
    async def expand_content_reference(
        self, content_ref: ContentReference, turn_id: str
    ) -> str: ...
    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool: ...
    async def delete_content_by_room_id(self, room_id: str) -> int: ...
    async def get_content_stats_for_room(self, room_id: str) -> dict: ...


class ContentStorageService:
    def __init__(self, delegate: ContentStorageDelegate | None = None) -> None:
        self._delegate = delegate

    def bind(self, delegate: ContentStorageDelegate) -> None:
        self._delegate = delegate

    def bind_facade(
        self,
        facade: object,
        *,
        platform_storage: ContentStorageDelegate | None = None,
    ) -> None:
        if platform_storage is None:
            raise RuntimeError(
                "ContentStorageService.bind_facade() requires platform_storage"
            )
        self.bind(platform_storage)

    def _require_delegate(self) -> ContentStorageDelegate:
        if self._delegate is None:
            raise RuntimeError(
                "ContentStorageService.bind_facade() not called - startup incomplete"
            )
        return self._delegate

    async def upsert_full_content(
        self,
        room_id: str,
        turn_id: str,
        content: str,
        content_type: str,
        turn_notes: dict | None = None,
    ) -> str:
        return await self._require_delegate().upsert_full_content(
            room_id,
            turn_id,
            content,
            content_type,
            turn_notes,
        )

    async def get_content_by_document_id(self, document_id: str) -> str | None:
        return await self._require_delegate().get_content_by_document_id(document_id)

    async def get_content_by_turn_id(
        self, room_id: str, turn_id: str
    ) -> str | None:
        return await self._require_delegate().get_content_by_turn_id(room_id, turn_id)

    async def expand_content_reference(
        self, content_ref: ContentReference, turn_id: str
    ) -> str:
        return await self._require_delegate().expand_content_reference(content_ref, turn_id)

    async def delete_content_by_turn_id(
        self, room_id: str, turn_id: str
    ) -> bool:
        return await self._require_delegate().delete_content_by_turn_id(room_id, turn_id)

    async def delete_content_by_room_id(self, room_id: str) -> int:
        return await self._require_delegate().delete_content_by_room_id(room_id)

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        return await self._require_delegate().get_content_stats_for_room(room_id)


content_storage_service = ContentStorageService()
