"""
API Key Model for Discovery API Authentication

Stores hashed API keys for external API access.
Keys are hashed with SHA-256 before storage - plaintext keys are never stored.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from common.utils.time import utcnow


class APIKey(BaseModel):
    """Model representing an API key for external API access."""

    # Unique identifier for the key
    key_id: str

    # SHA-256 hash of the API key (never store plaintext)
    key_hash: str

    # Owner of the key (user ID or organization ID)
    user_id: str

    # Friendly name for the key (e.g., "Production Key", "Development")
    name: str

    # Timestamps
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime | None = None

    # Key status
    is_active: bool = True

    # Usage tracking
    usage_count: int = 0

