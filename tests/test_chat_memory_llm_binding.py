from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app_shell.memory_service import ChatMemoryService
from common.dto import ChatContextGenerationInput
from models.memory import ChatContext, ContextData
from models.request import ChatMemoryRequest


@pytest.mark.asyncio
async def test_chat_memory_update_uses_bound_room_memory_llm_service():
    service = ChatMemoryService()
    service._store = MagicMock()
    service._store.get_chat_context_by_session_id = AsyncMock(
        return_value=ChatContext(
            memory_id="mem-1",
            user_name="user",
            session_id="session-1",
            context_data=ContextData(context_content="existing"),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            extend_info=[],
        )
    )
    service._store.update_chat_context_by_session_id = AsyncMock(
        return_value=True
    )
    service.room_memory_llm_service = MagicMock()
    service.room_memory_llm_service.generate_chat_context = AsyncMock(
        return_value="focused context"
    )

    result = await service.update_chat_context_by_session_id(
        ChatMemoryRequest(
            user_name="user",
            session_id="session-1",
            user_input="hello",
            agent_response="hi",
        )
    )

    assert result.success is True
    service.room_memory_llm_service.generate_chat_context.assert_awaited_once_with(
        ChatContextGenerationInput(
            user_input="hello",
            agent_response="hi",
            existing_context="existing",
        )
    )
