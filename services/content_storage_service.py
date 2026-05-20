"""
Content Storage Service for lossless compaction.

This service handles storing and retrieving full content for compacted turns.
Content is stored in MongoDB's conversation_content collection.

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.3, §6.4, §6.6 for design details.
"""

from database.mongodb import mongodb
from common.utils.logger import get_logger
from models.compaction import ContentReference, StorageType
from platform_module import PlatformConfig, PlatformDeps
from platform_module.content_storage import (
    ContentExpiredError,
    PlatformContentStorage,
    hash_content,
)

logger = get_logger(__name__)


class _FacadeContentStorageRepository:
    def __init__(self, facade) -> None:
        self._facade = facade

    async def upsert_full_content(self, **kwargs) -> str:
        return await self._facade.content_upsert_full_content(
            room_id=kwargs["room_id"],
            turn_id=kwargs["turn_id"],
            content=kwargs["content"],
            content_type=kwargs["content_type"],
            turn_notes=kwargs.get("turn_notes"),
        )

    async def get_content_by_document_id(self, document_id: str) -> dict | None:
        content = await self._facade.content_get_content_by_document_id(document_id)
        return {"content": content} if content is not None else None

    async def get_content_by_turn_id(
        self, room_id: str, turn_id: str
    ) -> dict | None:
        content = await self._facade.content_get_content_by_turn_id(room_id, turn_id)
        return {"content": content} if content is not None else None

    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool:
        return await self._facade.content_delete_content_by_turn_id(room_id, turn_id)

    async def delete_content_by_room_id(self, room_id: str) -> int:
        return await self._facade.content_delete_content_by_room_id(room_id)

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        return await self._facade.content_get_content_stats_for_room(room_id)

    async def text_search(self, room_id: str, query: str, limit: int = 50) -> list[dict]:
        return []

    async def hydrate_turn_notes(
        self, room_id: str, turn_ids: list[str]
    ) -> list[dict]:
        return []


class _LegacyS3TextObjectStorage:
    async def download_text(self, key: str) -> str | None:
        from services.s3_service import s3_service

        return await s3_service.download_text(key)


class ContentStorageService:
    """
    Service for storing and retrieving full content for compacted turns.

    Implements the storage layer for lossless compaction:
    - Store full content in MongoDB with idempotent upsert
    - Retrieve content by document ID
    - Support TTL-based expiry (optional)

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.6 for schema.
    """

    def __init__(self):
        self._collection_name = "conversation_content"
        self._facade = None
        self._platform_storage_override = None
        self._bound = False

    def bind_facade(self, facade, *, platform_storage=None) -> None:
        self._facade = facade
        self._platform_storage_override = platform_storage
        self._bound = True

    def _require_facade(self):
        if not self._bound or self._facade is None:
            raise RuntimeError(
                "ContentStorageService.bind_facade() not called - startup incomplete"
            )
        return self._facade

    def _platform_storage(self, *, require_facade: bool) -> PlatformContentStorage:
        if self._platform_storage_override is not None:
            return self._platform_storage_override
        facade = self._require_facade() if require_facade else self._facade
        repository = (
            _FacadeContentStorageRepository(facade)
            if facade is not None
            else None
        )
        return PlatformContentStorage(
            config=PlatformConfig(),
            deps=PlatformDeps(
                content_storage_repository=repository,
                object_storage=_LegacyS3TextObjectStorage(),
                logger=logger,
            ),
        )

    @property
    def collection(self):
        """Get the conversation_content collection."""
        return mongodb.conversation_content_collection

    async def upsert_full_content(
        self,
        room_id: str,
        turn_id: str,
        content: str,
        content_type: str,
        turn_notes: dict | None = None,
    ) -> str:
        """
        Store full content idempotently. Returns the document_id.

        Uses update_one(upsert=True) on the unique (room_id, turn_id) index.
        If a document already exists for this turn (e.g., from a previous crashed
        compaction run), returns its existing _id without creating a duplicate.

        See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.3 for specification.

        Args:
            room_id: The room ID
            turn_id: The turn ID (unique within room)
            content: Full text content to store
            content_type: Type of content ("text", "tool_result", "agent_response")
            turn_notes: Optional structured notes for keyword search

        Returns:
            The document ID (string)
        """
        return await self._platform_storage(
            require_facade=True
        ).upsert_full_content(
            room_id,
            turn_id,
            content,
            content_type,
            turn_notes,
        )

    async def get_content_by_document_id(self, document_id: str) -> str | None:
        """
        Retrieve full content by document ID.

        Args:
            document_id: The MongoDB document ID

        Returns:
            The full content string, or None if not found
        """
        return await self._platform_storage(
            require_facade=True
        ).get_content_by_document_id(document_id)

    async def get_content_by_turn_id(
        self, room_id: str, turn_id: str
    ) -> str | None:
        """
        Retrieve full content by room_id and turn_id.

        Args:
            room_id: The room ID
            turn_id: The turn ID

        Returns:
            The full content string, or None if not found
        """
        return await self._platform_storage(
            require_facade=True
        ).get_content_by_turn_id(room_id, turn_id)

    async def expand_content_reference(
        self, content_ref: ContentReference, turn_id: str
    ) -> str:
        """
        Expand a ContentReference to retrieve the full content.

        Args:
            content_ref: The content reference with storage location
            turn_id: The turn ID (for error messages)

        Returns:
            The full content string

        Raises:
            ContentExpiredError: If the content is not found
            NotImplementedError: If the storage type is not yet implemented (URL)
            ValueError: If the content reference is malformed
        """
        return await self._platform_storage(
            require_facade=content_ref.storage_type == StorageType.MONGODB
        ).expand_content_reference(content_ref, turn_id)

    async def delete_content_by_turn_id(
        self, room_id: str, turn_id: str
    ) -> bool:
        """
        Delete stored content for a turn.

        Args:
            room_id: The room ID
            turn_id: The turn ID

        Returns:
            True if content was deleted, False if not found
        """
        return await self._platform_storage(
            require_facade=True
        ).delete_content_by_turn_id(room_id, turn_id)

    async def delete_content_by_room_id(self, room_id: str) -> int:
        """
        Delete all stored content for a room.

        Args:
            room_id: The room ID

        Returns:
            Number of documents deleted
        """
        return await self._platform_storage(
            require_facade=True
        ).delete_content_by_room_id(room_id)

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        """
        Get statistics about stored content for a room.

        Args:
            room_id: The room ID

        Returns:
            Dict with content statistics
        """
        return await self._platform_storage(
            require_facade=True
        ).get_content_stats_for_room(room_id)


# Singleton export
content_storage_service = ContentStorageService()
