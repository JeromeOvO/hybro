"""
Compaction models for lossless context compression.

This module implements pointer-based compaction (NOT summarization).
Full content is stored in MongoDB and replaced with references in context.
Original content is always retrievable on demand.

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6 for design details.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from common.utils.time import utcnow


class StorageType(str, Enum):
    """
    Storage backend type for content references.

    MONGODB: Text content stored in MongoDB (current implementation)
    S3: Binary content stored in S3 (future extension)
    URL: Web content referenced by URL

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.2 for specification.
    """

    MONGODB = "mongodb"
    S3 = "s3"
    URL = "url"


class ContentReference(BaseModel):
    """
    Pointer to full content in storage. Used for compact representation.

    Current implementation: MongoDB for text content
    Future extension: S3 for binary content (images, files, video)

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.2 for specification.
    """

    storage_type: StorageType

    # MongoDB reference (for text content)
    collection: str | None = None
    document_id: str | None = None

    # S3 reference (FUTURE: for binary content)
    s3_bucket: str | None = None
    s3_key: str | None = None

    # URL reference (for web content)
    url: str | None = None

    # Metadata for retrieval
    content_hash: str | None = None  # For cache validation
    mime_type: str | None = None  # e.g., "text/plain", "image/png"
    size_bytes: int | None = None  # For binary content
    created_at: datetime = Field(default_factory=utcnow)

    def to_compact_string(self) -> str:
        """Generate compact representation for context."""
        if self.storage_type == StorageType.MONGODB:
            return f"[Content stored: db/{self.collection}/{self.document_id}]"
        elif self.storage_type == StorageType.S3:
            return f"[Content stored: s3://{self.s3_bucket}/{self.s3_key}]"
        elif self.storage_type == StorageType.URL:
            return f"[Content from: {self.url}]"
        return "[Content reference]"


class StoredContent(BaseModel):
    """
    Full text content stored for compacted turns.

    Stored in MongoDB `conversation_content` collection.
    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.6 for schema.
    """

    id: str = Field(alias="_id")
    room_id: str
    turn_id: str
    content: str  # Full text content
    content_type: (
        str  # "text", "tool_result", "agent_response" (uses memory.ContentType values)
    )
    content_hash: str  # SHA-256 for integrity/deduplication
    stored_at: datetime = Field(default_factory=utcnow)

    # TTL: None (keep forever) or set retention policy
    expires_at: datetime | None = None

    # Optional: turn_notes for keyword search on compact turns
    turn_notes: dict | None = None

    class Config:
        populate_by_name = True


class CompactionResult(BaseModel):
    """Result of a compaction operation."""

    room_id: str
    compacted_count: int  # Number of turns compacted
    tokens_saved: int  # Estimated tokens saved
    errors: list[str] = Field(default_factory=list)  # Any errors encountered
    compacted_at: datetime = Field(default_factory=utcnow)
