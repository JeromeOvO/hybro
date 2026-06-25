from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.dto import ChatContextGenerationInput
from context_memory.compat.runtime import ContextMemoryChatAdapter
from models.error import SessionIdRequiredError
from models.memory import ChatContext, ContextData
from models.request import ChatMemoryRequest


def test_context_memory_chat_adapter_requires_explicit_store():
    with pytest.raises(
        RuntimeError,
        match=r"ContextMemoryChatAdapter requires chat_store",
    ):
        ContextMemoryChatAdapter(chat_store=None)


@pytest.mark.asyncio
async def test_chat_memory_create_requires_session_id_before_store_call():
    store = MagicMock()
    store.add_chat_context = AsyncMock()
    service = ContextMemoryChatAdapter(chat_store=store)

    with pytest.raises(SessionIdRequiredError):
        await service.create_chat_context(ChatMemoryRequest(user_name="user"))

    store.add_chat_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_memory_delete_requires_session_id_before_store_call():
    store = MagicMock()
    store.delete_chat_context_by_session_id = AsyncMock()
    service = ContextMemoryChatAdapter(chat_store=store)

    with pytest.raises(SessionIdRequiredError):
        await service.delete_chat_context_by_session_id(
            ChatMemoryRequest(user_name="user")
        )

    store.delete_chat_context_by_session_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_memory_update_uses_bound_room_memory_llm_service():
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    store = MagicMock()
    store.get_chat_context_by_session_id = AsyncMock(
        return_value=ChatContext(
            memory_id="mem-1",
            user_name="user",
            session_id="session-1",
            context_data=ContextData(context_content="existing"),
            created_at=created_at,
            updated_at=created_at,
            extend_info=[],
        )
    )
    store.update_chat_context_by_session_id = AsyncMock(return_value=True)
    llm_service = MagicMock()
    llm_service.generate_chat_context = AsyncMock(return_value="focused context")
    service = ContextMemoryChatAdapter(
        chat_store=store,
        chat_context_llm=llm_service,
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
    llm_service.generate_chat_context.assert_awaited_once_with(
        ChatContextGenerationInput(
            user_input="hello",
            agent_response="hi",
            existing_context="existing",
        )
    )
    store.update_chat_context_by_session_id.assert_awaited_once()
    session_id, payload = store.update_chat_context_by_session_id.await_args.args
    assert session_id == "session-1"
    assert payload.memory_id == "mem-1"
    assert payload.session_id == "session-1"
    assert payload.created_at == created_at
    assert payload.extend_info == []
