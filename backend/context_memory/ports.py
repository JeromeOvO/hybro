"""Persistence ports Context Memory consumes."""

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class MemoryRepository(Protocol):
    async def get_room_memory(self, conversation_id: str) -> dict | None: ...
    async def upsert_room_memory(self, conversation_id: str, memory: dict) -> None: ...
    async def delete_room_memory(self, conversation_id: str) -> bool: ...
    async def create_room_memory(self, memory: dict) -> str: ...
    async def ensure_room_memory(
        self, conversation_id: str, defaults: dict
    ) -> dict: ...
    async def push_and_trim_conversation_turn_if_absent(
        self,
        conversation_id: str,
        turn: dict,
        *,
        turn_id: str,
        max_turns: int,
        summary_stub: str,
        max_summary_chars: int,
    ) -> tuple[bool, bool, bool]: ...
    async def compact_turns_bulk(
        self, conversation_id: str, compacted_turns: list[dict]
    ) -> bool: ...
    async def list_room_ids_with_memory(
        self, limit: int | None = None
    ) -> list[str]: ...


@runtime_checkable
class ContentStorageRepository(Protocol):
    async def upsert_full_content(
        self,
        *,
        document_id: str,
        conversation_id: str,
        turn_id: str,
        content: str,
        content_type: str,
        content_hash: str,
        stored_at: datetime,
        turn_timestamp: datetime | str | None = None,
        expires_at: datetime | None = None,
    ) -> str: ...
    async def get_content_by_turn_id(
        self, conversation_id: str, turn_id: str
    ) -> dict | None: ...
    async def delete_content_by_turn_id(
        self, conversation_id: str, turn_id: str
    ) -> bool: ...
    async def delete_content_by_room_id(self, conversation_id: str) -> int: ...


__all__ = [
    "ContentStorageRepository",
    "MemoryRepository",
]
