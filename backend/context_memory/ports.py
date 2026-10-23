"""Persistence ports Context Memory consumes."""

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class MemoryRepository(Protocol):
    async def get_room_memory(self, room_id: str) -> dict | None: ...
    async def upsert_room_memory(self, room_id: str, memory: dict) -> None: ...
    async def delete_room_memory(self, room_id: str) -> bool: ...
    async def create_room_memory(self, memory: dict) -> str: ...
    async def ensure_room_memory(self, room_id: str, defaults: dict) -> dict: ...
    async def push_and_trim_conversation_turn(
        self,
        room_id: str,
        turn: dict,
        *,
        max_turns: int,
        summary_stub: str,
        max_summary_chars: int,
    ) -> tuple[bool, bool]: ...
    async def push_and_trim_conversation_turn_if_absent(
        self,
        room_id: str,
        turn: dict,
        *,
        turn_id: str,
        max_turns: int,
        summary_stub: str,
        max_summary_chars: int,
    ) -> tuple[bool, bool, bool]: ...
    async def update_turn_notes(
        self, room_id: str, turn_id: str, turn_notes: dict
    ) -> bool: ...
    async def get_room_summary_projection(self, room_id: str) -> dict | None: ...
    async def update_room_summary_atomic(
        self,
        room_id: str,
        room_summary: dict,
        *,
        new_facts: list[dict] | None = None,
        max_facts: int = 50,
    ) -> bool: ...
    async def compact_turns_bulk(
        self, room_id: str, compacted_turns: list[dict]
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
        room_id: str,
        turn_id: str,
        content: str,
        content_type: str,
        content_hash: str,
        stored_at: datetime,
        turn_timestamp: datetime | str | None = None,
        expires_at: datetime | None = None,
        turn_notes: dict | None = None,
    ) -> str: ...
    async def get_content_by_document_id(self, document_id: str) -> dict | None: ...
    async def get_content_by_turn_id(
        self, room_id: str, turn_id: str
    ) -> dict | None: ...
    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool: ...
    async def delete_content_by_room_id(self, room_id: str) -> int: ...
    async def get_content_stats_for_room(self, room_id: str) -> dict: ...
    async def text_search(
        self,
        room_id: str,
        query: str,
        limit: int = 50,
        skip: int = 0,
    ) -> list[dict]: ...
    async def scan_text_search(
        self, room_id: str, query: str, limit: int
    ) -> list[dict]: ...
    async def hydrate_turn_content(
        self, room_id: str, turn_ids: list[str]
    ) -> list[dict]: ...


__all__ = [
    "ContentStorageRepository",
    "MemoryRepository",
]
