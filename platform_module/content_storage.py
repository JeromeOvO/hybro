from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from platform_module.config import PlatformConfig
from platform_module.deps import PlatformDeps


class ContentExpiredError(Exception):
    def __init__(self, turn_id: str, document_id: str):
        self.turn_id = turn_id
        self.document_id = document_id
        super().__init__(
            f"Content for turn {turn_id} (doc {document_id}) not found in storage"
        )


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def make_document_id(room_id: str, turn_id: str) -> str:
    return f"conversation_content:{room_id}:{turn_id}"


class PlatformContentStorage:
    def __init__(self, config: PlatformConfig, deps: PlatformDeps) -> None:
        self._config = config
        self._deps = deps

    async def upsert_full_content(
        self,
        room_id: str,
        turn_id: str,
        content: str,
        content_type: str,
        turn_notes: dict | None = None,
    ) -> str:
        now = self._deps.clock()
        expires_at = None
        if self._config.content_storage_ttl_seconds > 0:
            expires_at = now + timedelta(
                seconds=self._config.content_storage_ttl_seconds
            )
        return await self._require_repository().upsert_full_content(
            document_id=make_document_id(room_id, turn_id),
            room_id=room_id,
            turn_id=turn_id,
            content=content,
            content_type=content_type,
            content_hash=hash_content(content),
            stored_at=now,
            expires_at=expires_at,
            turn_notes=turn_notes,
        )

    async def get_content_by_document_id(self, document_id: str) -> str | None:
        doc = await self._require_repository().get_content_by_document_id(
            document_id
        )
        if not doc or self._is_expired(doc):
            return None
        return doc.get("content")

    async def get_content_by_turn_id(
        self, room_id: str, turn_id: str
    ) -> str | None:
        doc = await self._require_repository().get_content_by_turn_id(
            room_id, turn_id
        )
        if not doc or self._is_expired(doc):
            return None
        return doc.get("content")

    async def expand_content_reference(self, content_ref: Any, turn_id: str) -> str:
        ref = self._to_dict(content_ref)
        storage_type = self._storage_type(ref)
        if storage_type == "mongodb":
            return await self._expand_mongodb_reference(ref, turn_id)
        if storage_type == "s3":
            return await self._expand_s3_reference(ref, turn_id)
        if storage_type == "url":
            raise NotImplementedError("URL expansion not yet implemented")
        raise ValueError(f"Unknown storage type: {storage_type}")

    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool:
        return await self._require_repository().delete_content_by_turn_id(
            room_id, turn_id
        )

    async def delete_content_by_room_id(self, room_id: str) -> int:
        return await self._require_repository().delete_content_by_room_id(room_id)

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        return await self._require_repository().get_content_stats_for_room(room_id)

    async def _expand_mongodb_reference(
        self, content_ref: dict[str, Any], turn_id: str
    ) -> str:
        document_id = content_ref.get("document_id")
        if not document_id:
            raise ValueError(
                f"ContentReference for turn {turn_id} has no document_id"
            )
        doc = await self._require_repository().get_content_by_document_id(
            document_id
        )
        if not doc or self._is_expired(doc):
            raise ContentExpiredError(turn_id, document_id)
        return doc.get("content") or ""

    def _is_expired(self, doc: dict[str, Any]) -> bool:
        expires_at = doc.get("expires_at")
        if not isinstance(expires_at, datetime):
            return False
        return _as_utc_aware(expires_at) <= _as_utc_aware(self._deps.clock())

    async def _expand_s3_reference(
        self, content_ref: dict[str, Any], turn_id: str
    ) -> str:
        s3_key = content_ref.get("s3_key")
        if not s3_key:
            raise ValueError(f"ContentReference for turn {turn_id} has no s3_key")

        object_storage = self._deps.object_storage
        if object_storage is None:
            raise RuntimeError("Platform content storage requires object_storage")
        if hasattr(object_storage, "download_text"):
            content = await object_storage.download_text(s3_key)
        elif hasattr(object_storage, "get_text"):
            content = await object_storage.get_text(s3_key)
        else:
            raise RuntimeError("Object storage cannot read text content")
        if content is None:
            raise ContentExpiredError(turn_id, s3_key)
        return content

    def _require_repository(self):
        if self._deps.content_storage_repository is None:
            raise RuntimeError(
                "Platform content storage requires content_storage_repository"
            )
        return self._deps.content_storage_repository

    @staticmethod
    def _to_dict(content_ref: Any) -> dict[str, Any]:
        if hasattr(content_ref, "model_dump"):
            return content_ref.model_dump(mode="json")
        return dict(content_ref)

    @staticmethod
    def _storage_type(content_ref: dict[str, Any]) -> str:
        storage_type = content_ref.get("storage_type")
        if hasattr(storage_type, "value"):
            return storage_type.value
        return str(storage_type)


def _as_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "ContentExpiredError",
    "PlatformContentStorage",
    "hash_content",
    "make_document_id",
]
