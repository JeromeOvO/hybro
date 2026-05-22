from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from common.protocols import ContentStorageRepository

from context_memory.config import CompactionConfig


class ContentExpiredError(Exception):
    def __init__(self, turn_id: str, document_id: str):
        self.turn_id = turn_id
        self.document_id = document_id
        super().__init__(
            f"Content for turn {turn_id} (doc {document_id}) not found in storage"
        )


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def store_full_content(
    repository: ContentStorageRepository,
    *,
    room_id: str,
    turn_id: str,
    content: str,
    content_type: str,
    turn_notes: dict | None,
    now: datetime,
    config: CompactionConfig,
) -> str:
    expires_delta = config.expires_delta()
    expires_at = now + expires_delta if expires_delta is not None else None
    document_id = make_document_id(room_id, turn_id)
    return await repository.upsert_full_content(
        document_id=document_id,
        room_id=room_id,
        turn_id=turn_id,
        content=content,
        content_type=content_type,
        content_hash=hash_content(content),
        stored_at=now,
        expires_at=expires_at,
        turn_notes=turn_notes,
    )


def make_document_id(room_id: str, turn_id: str) -> str:
    return f"conversation_content:{room_id}:{turn_id}"


async def expand_mongodb_reference(
    repository: ContentStorageRepository,
    content_ref: dict[str, Any],
    turn_id: str,
    *,
    now: datetime | None = None,
) -> str:
    document_id = content_ref.get("document_id")
    if not document_id:
        raise ValueError(f"ContentReference for turn {turn_id} has no document_id")
    doc = await repository.get_content_by_document_id(document_id)
    if not doc or is_content_expired(doc, now=now):
        raise ContentExpiredError(turn_id, document_id)
    return doc.get("content") or ""


def content_from_document(doc: dict | None, *, now: datetime | None = None) -> str | None:
    if not doc or is_content_expired(doc, now=now):
        return None
    return doc.get("content")


def is_content_expired(doc: dict[str, Any], *, now: datetime | None = None) -> bool:
    expires_at = doc.get("expires_at")
    if not isinstance(expires_at, datetime):
        return False
    reference = now or datetime.now(timezone.utc)
    return _as_utc_aware(expires_at) <= _as_utc_aware(reference)


def _as_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compact_pointer(content_ref: dict[str, Any]) -> str:
    storage_type = content_ref.get("storage_type")
    if storage_type == "mongodb":
        return (
            "[Content stored: db/"
            f"{content_ref.get('collection')}/{content_ref.get('document_id')}]"
        )
    if storage_type == "s3":
        return (
            "[Content stored: s3://"
            f"{content_ref.get('s3_bucket')}/{content_ref.get('s3_key')}]"
        )
    if storage_type == "url":
        return f"[Content from: {content_ref.get('url')}]"
    return "[Content reference]"
