from __future__ import annotations

from models.request import ChatMemoryRequest
from models.response import ChatMemoryResponse
from app_shell.memory_service import chat_memory_service


class AppShellMemoryCenter:
    def __init__(self, service=None):
        self.chat_memory_service = service or chat_memory_service

    async def add_chat_context(self, request: ChatMemoryRequest) -> ChatMemoryResponse:
        return await self.chat_memory_service.create_chat_context(request)

    async def get_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        return await self.chat_memory_service.get_chat_context_by_session_id(request)

    async def update_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        return await self.chat_memory_service.update_chat_context_by_session_id(request)

    async def delete_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        return await self.chat_memory_service.delete_chat_context_by_session_id(request)


__all__ = ["AppShellMemoryCenter"]
