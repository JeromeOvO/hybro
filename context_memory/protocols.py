from __future__ import annotations

from typing import Protocol, runtime_checkable

from common.dto import CompactionResult
from models.request import ChatMemoryRequest
from models.response import ChatMemoryResponse


@runtime_checkable
class LegacyChatContextAPI(Protocol):
    async def add_chat_context(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse: ...
    async def get_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse: ...
    async def update_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse: ...
    async def delete_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse: ...


@runtime_checkable
class ContextMemoryCompactionPort(Protocol):
    async def should_compact(self, room_id: str) -> bool: ...
    async def compact_if_needed(self, room_id: str) -> CompactionResult | None: ...
    async def compact_room_memory(
        self,
        room_id: str,
        room_memory: object | None = None,
    ) -> CompactionResult: ...


__all__ = ["ContextMemoryCompactionPort", "LegacyChatContextAPI"]
