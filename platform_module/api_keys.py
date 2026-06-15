"""
Platform-owned API Key Store

Provides API key management using MongoDAL for storage.
Implements both APIKeyStore (for management) and APIKeyValidationStore (for auth).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from common.protocols import APIKeyRecord, MongoDAL

_API_KEY_FIELDS = frozenset(
    ("key_id", "user_id", "name", "is_active", "created_at", "last_used_at", "usage_count", "key_hash")
)


@dataclass
class APIKeyData:
    """Internal data class for API keys that implements APIKeyRecord protocol."""

    key_id: str
    user_id: str
    name: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None
    usage_count: int
    key_hash: str


class MongoAPIKeyStore:
    """MongoDB implementation of API key storage using MongoDAL."""

    def __init__(self, mongo: MongoDAL) -> None:
        self._mongo = mongo
        self._collection = mongo.collection("api_keys")

    async def get_api_key_by_hash(self, key_hash: str) -> APIKeyRecord | None:
        """
        Get an API key by its hash (for authentication).

        Args:
            key_hash: SHA-256 hash of the API key

        Returns:
            APIKeyRecord or None if not found
        """
        doc = await self._collection.find_one({"key_hash": key_hash})
        return APIKeyData(**{k: v for k, v in doc.items() if k in _API_KEY_FIELDS}) if doc else None

    async def get_api_key_by_id(self, key_id: str) -> APIKeyRecord | None:
        """
        Get an API key by its ID.

        Args:
            key_id: The key ID

        Returns:
            APIKeyRecord or None if not found
        """
        doc = await self._collection.find_one({"key_id": key_id})
        return APIKeyData(**{k: v for k, v in doc.items() if k in _API_KEY_FIELDS}) if doc else None

    async def get_api_keys_by_user(self, user_id: str) -> list[APIKeyRecord]:
        """
        Get all API keys for a user.

        Args:
            user_id: The user ID

        Returns:
            List of APIKeyRecord instances
        """
        docs = await self._collection.find({"user_id": user_id})
        return [APIKeyData(**{k: v for k, v in doc.items() if k in _API_KEY_FIELDS}) for doc in docs]

    async def add_api_key(self, api_key: APIKeyRecord) -> str:
        """
        Add an API key to the database.

        Args:
            api_key: APIKeyRecord instance

        Returns:
            str: The key_id of the inserted key
        """
        # Convert the protocol object to a dict for MongoDB
        doc = {
            "key_id": api_key.key_id,
            "user_id": api_key.user_id,
            "name": api_key.name,
            "is_active": api_key.is_active,
            "created_at": api_key.created_at,
            "last_used_at": api_key.last_used_at,
            "usage_count": api_key.usage_count,
            "key_hash": api_key.key_hash,
        }
        await self._collection.insert_one(doc)
        return api_key.key_id

    async def deactivate_api_key(self, key_id: str) -> bool:
        """
        Deactivate an API key.

        Args:
            key_id: The key ID

        Returns:
            bool: True if deactivation was successful
        """
        return await self._collection.update_one(
            {"key_id": key_id},
            {"$set": {"is_active": False}},
        )

    async def update_api_key_usage(self, key_hash: str) -> bool:
        """
        Update the usage statistics for an API key.
        Increments usage_count and sets last_used_at.

        Args:
            key_hash: SHA-256 hash of the API key

        Returns:
            bool: True if update was successful
        """
        from common.utils.time import utcnow

        return await self._collection.update_one(
            {"key_hash": key_hash},
            {
                "$set": {"last_used_at": utcnow()},
                "$inc": {"usage_count": 1},
            },
        )


__all__ = ["MongoAPIKeyStore"]
