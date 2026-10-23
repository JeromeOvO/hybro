from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from context_memory.config import CompactionConfig
from context_memory.ports import ContentStorageRepository


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def store_full_content(
    repository: ContentStorageRepository,
    *,
    conversation_id: str,
    turn_id: str,
    content: str,
    content_type: str,
    turn_timestamp: datetime | str | None = None,
    now: datetime,
    config: CompactionConfig,
) -> str:
    expires_delta = config.expires_delta()
    expires_at = now + expires_delta if expires_delta is not None else None
    document_id = make_document_id(conversation_id, turn_id)
    return await repository.upsert_full_content(
        document_id=document_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        content=content,
        content_type=content_type,
        content_hash=hash_content(content),
        stored_at=now,
        turn_timestamp=turn_timestamp,
        expires_at=expires_at,
    )


def make_document_id(conversation_id: str, turn_id: str) -> str:
    return f"conversation_content:{conversation_id}:{turn_id}"


def is_content_expired(doc: dict[str, Any], *, now: datetime | None = None) -> bool:
    expires_at = doc.get("expires_at")
    if not isinstance(expires_at, datetime):
        return False
    reference = now or datetime.now(UTC)
    return _as_utc_aware(expires_at) <= _as_utc_aware(reference)


def _as_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
