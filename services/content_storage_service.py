"""
Content Storage Service for lossless compaction.

This service handles storing and retrieving full content for compacted turns.
Content is stored in MongoDB's conversation_content collection.

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.3, §6.4, §6.6 for design details.
"""

from common.utils.logger import get_logger
from context_memory.content_storage import ContentExpiredError, hash_content
from database.mongodb import mongodb
from models.compaction import ContentReference, StorageType

logger = get_logger(__name__)


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
        self._bound = False

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def _require_facade(self):
        if not self._bound or self._facade is None:
            raise RuntimeError(
                "ContentStorageService.bind_facade() not called - startup incomplete"
            )
        return self._facade

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
        facade = self._require_facade()
        return await facade.content_upsert_full_content(
            room_id=room_id,
            turn_id=turn_id,
            content=content,
            content_type=content_type,
            turn_notes=turn_notes,
        )

    async def get_content_by_document_id(self, document_id: str) -> str | None:
        """
        Retrieve full content by document ID.

        Args:
            document_id: The MongoDB document ID

        Returns:
            The full content string, or None if not found
        """
        facade = self._require_facade()
        return await facade.content_get_content_by_document_id(document_id)

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
        facade = self._require_facade()
        return await facade.content_get_content_by_turn_id(room_id, turn_id)

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
        if content_ref.storage_type == StorageType.MONGODB:
            facade = self._require_facade()
            return await facade.content_expand_mongodb_reference(
                content_ref.model_dump(mode="json"),
                turn_id,
            )

        elif content_ref.storage_type == StorageType.S3:
            if not content_ref.s3_key:
                raise ValueError(
                    f"ContentReference for turn {turn_id} has no s3_key"
                )

            from services.s3_service import s3_service

            content = await s3_service.download_text(content_ref.s3_key)
            if content is None:
                raise ContentExpiredError(turn_id, content_ref.s3_key)
            return content

        elif content_ref.storage_type == StorageType.URL:
            # FUTURE: URL-based content retrieval (external web content).
            # Blocked due to SSRF risk — requires allow-listing, timeout,
            # size limits, and redirect controls before enabling.
            # See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.8 for design notes.
            raise NotImplementedError("URL expansion not yet implemented")

        else:
            raise ValueError(
                f"Unknown storage type: {content_ref.storage_type}"
            )

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
        facade = self._require_facade()
        return await facade.content_delete_content_by_turn_id(
            room_id, turn_id
        )

    async def delete_content_by_room_id(self, room_id: str) -> int:
        """
        Delete all stored content for a room.

        Args:
            room_id: The room ID

        Returns:
            Number of documents deleted
        """
        facade = self._require_facade()
        return await facade.content_delete_content_by_room_id(room_id)

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        """
        Get statistics about stored content for a room.

        Args:
            room_id: The room ID

        Returns:
            Dict with content statistics
        """
        facade = self._require_facade()
        return await facade.content_get_content_stats_for_room(room_id)


# Singleton export
content_storage_service = ContentStorageService()
