from models.request import ChatMemoryRequest
from models.response import ChatMemoryResponse
from services.memory_service import chat_memory_service


class MemoryCenter:
    def __init__(self):
        self.chat_memory_service = chat_memory_service  # Use singleton

    async def add_chat_context(self, request: ChatMemoryRequest) -> ChatMemoryResponse:
        """
        Add a chat context to the memory center.
        """
        return await self.chat_memory_service.create_chat_context(request)

    async def get_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        """
        Get a chat context by session_id.
        """
        return await self.chat_memory_service.get_chat_context_by_session_id(request)

    async def update_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        """
        Update a chat context by session_id.
        """
        return await self.chat_memory_service.update_chat_context_by_session_id(request)

    async def delete_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        """
        Delete a chat context by session_id.
        """
        return await self.chat_memory_service.delete_chat_context_by_session_id(request)
