from __future__ import annotations

from typing import Protocol, runtime_checkable

from models.request import ChatMemoryRequest
from models.response import ChatMemoryResponse


@runtime_checkable
class LegacyChatContextAPI(Protocol):
    async def add_chat_context(self, request: ChatMemoryRequest) -> ChatMemoryResponse: ...
    async def get_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse: ...
    async def update_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse: ...
    async def delete_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse: ...


__all__ = ["LegacyChatContextAPI"]
