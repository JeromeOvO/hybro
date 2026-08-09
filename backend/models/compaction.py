"""
Compaction models for lossless context compression.

This module implements pointer-based compaction (NOT summarization).
Full content is stored in MongoDB and replaced with references in context.
Original content is always retrievable on demand.

See docs/System-Architecture.md for the current design.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from common.utils.time import utcnow


class StorageType(str, Enum):
    """
    Storage backend type for content references.

    MONGODB: Text content stored in MongoDB (current implementation)
    URL: Web content referenced by URL

    See docs/System-Architecture.md for the current architecture.
    """

    MONGODB = "mongodb"
    URL = "url"


class ContentReference(BaseModel):
    """
    Pointer to full content in storage. Used for compact representation.

    Current implementation: MongoDB for text content
    See docs/System-Architecture.md for the current architecture.
    """

    storage_type: StorageType

    # MongoDB reference (for text content)
    collection: str | None = None
    document_id: str | None = None

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
        elif self.storage_type == StorageType.URL:
            return f"[Content from: {self.url}]"
        return "[Content reference]"
