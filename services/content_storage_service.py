"""
Content Storage Service for lossless compaction.

This service handles storing and retrieving full content for compacted turns.
Content is stored in MongoDB's conversation_content collection.

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.3, §6.4, §6.6 for design details.
"""

import hashlib
from datetime import timedelta

from common.utils.logger import get_logger
from common.utils.time import utcnow
from config.settings import settings
from database.mongodb import mongodb
from models.compaction import ContentReference, StorageType, StoredContent

logger = get_logger(__name__)


class ContentExpiredError(Exception):
    """
    Raised when a compacted turn's stored content can no longer be retrieved.

    Callers should log the error and fall back to the compact pointer string
    rather than crashing the request. This indicates a data integrity issue
    (TTL expiry, manual deletion, or migration error) that needs investigation.

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.4 for specification.
    """

    def __init__(self, turn_id: str, document_id: str):
        self.turn_id = turn_id
        self.document_id = document_id
        super().__init__(
            f"Content for turn {turn_id} (doc {document_id}) not found in storage"
        )


def hash_content(content: str) -> str:
    """
    Generate SHA-256 hash of content for integrity/deduplication.

    Args:
        content: The text content to hash

    Returns:
        Hex-encoded SHA-256 hash
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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
        content_hash = hash_content(content)
        now = utcnow()

        # Calculate expiry if TTL is configured
        expires_at = None
        if settings.compaction_content_ttl_days > 0:
            expires_at = now + timedelta(days=settings.compaction_content_ttl_days)

        # Build the document for $setOnInsert
        insert_doc = {
            "room_id": room_id,
            "turn_id": turn_id,
            "content": content,
            "content_type": content_type,
            "content_hash": content_hash,
            "stored_at": now,
            "expires_at": expires_at,
        }

        if turn_notes:
            insert_doc["turn_notes"] = turn_notes

        result = await self.collection.update_one(
            {"room_id": room_id, "turn_id": turn_id},  # Filter on unique key
            {"$setOnInsert": insert_doc},  # Only set fields on insert
            upsert=True,
        )

        # For an insert: result.upserted_id is the new _id
        if result.upserted_id:
            doc_id = str(result.upserted_id)
            logger.debug(
                f"ContentStorageService: Stored new content for turn {turn_id}, "
                f"doc_id={doc_id}, hash={content_hash[:16]}..."
            )
            return doc_id

        # For an update (already existed): fetch the existing _id
        existing = await self.collection.find_one(
            {"room_id": room_id, "turn_id": turn_id}, {"_id": 1}
        )
        if existing:
            doc_id = str(existing["_id"])
            logger.debug(
                f"ContentStorageService: Content already exists for turn {turn_id}, "
                f"doc_id={doc_id}"
            )
            return doc_id

        # This should never happen if the upsert worked correctly
        raise RuntimeError(
            f"Failed to upsert content for turn {turn_id}: "
            "no upserted_id and no existing document"
        )

    async def get_content_by_document_id(self, document_id: str) -> str | None:
        """
        Retrieve full content by document ID.

        Args:
            document_id: The MongoDB document ID

        Returns:
            The full content string, or None if not found
        """
        from bson import ObjectId

        try:
            doc = await self.collection.find_one({"_id": ObjectId(document_id)})
            if doc:
                return doc.get("content")
            return None
        except Exception as e:
            logger.error(f"ContentStorageService: Error retrieving content: {e}")
            return None

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
        doc = await self.collection.find_one(
            {"room_id": room_id, "turn_id": turn_id}
        )
        if doc:
            return doc.get("content")
        return None

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
            if not content_ref.document_id:
                raise ValueError(
                    f"ContentReference for turn {turn_id} has no document_id"
                )

            content = await self.get_content_by_document_id(content_ref.document_id)
            if content is None:
                raise ContentExpiredError(turn_id, content_ref.document_id)
            return content

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
        result = await self.collection.delete_one(
            {"room_id": room_id, "turn_id": turn_id}
        )
        return result.deleted_count > 0

    async def delete_content_by_room_id(self, room_id: str) -> int:
        """
        Delete all stored content for a room.

        Args:
            room_id: The room ID

        Returns:
            Number of documents deleted
        """
        result = await self.collection.delete_many({"room_id": room_id})
        logger.info(
            f"ContentStorageService: Deleted {result.deleted_count} content documents "
            f"for room {room_id}"
        )
        return result.deleted_count

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        """
        Get statistics about stored content for a room.

        Args:
            room_id: The room ID

        Returns:
            Dict with content statistics
        """
        pipeline = [
            {"$match": {"room_id": room_id}},
            {
                "$group": {
                    "_id": "$content_type",
                    "count": {"$sum": 1},
                    "total_size": {"$sum": {"$strLenBytes": "$content"}},
                }
            },
        ]

        cursor = self.collection.aggregate(pipeline)
        results = await cursor.to_list(length=None)

        stats = {
            "room_id": room_id,
            "by_type": {},
            "total_documents": 0,
            "total_size_bytes": 0,
        }

        for result in results:
            content_type = result["_id"]
            stats["by_type"][content_type] = {
                "count": result["count"],
                "size_bytes": result["total_size"],
            }
            stats["total_documents"] += result["count"]
            stats["total_size_bytes"] += result["total_size"]

        return stats


# Singleton export
content_storage_service = ContentStorageService()
