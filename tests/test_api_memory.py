"""
Unit tests for api/memory_center.py endpoints.

Tests cover:
- add_chat_context: delegation to MemoryCenter
- get_chat_context_by_session_id: delegation
- update_chat_context_by_session_id: delegation with all fields
- delete_chat_context_by_session_id: delegation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.memory_center import (
    add_chat_context,
    get_chat_context_by_session_id,
    update_chat_context_by_session_id,
    delete_chat_context_by_session_id,
)

PATCH_MC = "api.memory_center.memory_center"


class TestAddChatContext:
    @pytest.mark.asyncio
    async def test_delegates_to_memory_center(self):
        request = MagicMock()
        request.json = AsyncMock(return_value={
            "user_name": "alice",
            "session_id": "sess-1",
            "user_input": "Hello",
        })
        expected = {"success": True}

        with patch(PATCH_MC) as mock_mc:
            mock_mc.add_chat_context = AsyncMock(return_value=expected)
            result = await add_chat_context(request)

        assert result == expected
        mock_mc.add_chat_context.assert_called_once()
        call_arg = mock_mc.add_chat_context.call_args[0][0]
        assert call_arg.user_name == "alice"
        assert call_arg.session_id == "sess-1"
        assert call_arg.user_input == "Hello"


class TestGetChatContextBySessionId:
    @pytest.mark.asyncio
    async def test_delegates_to_memory_center(self):
        request = MagicMock()
        request.json = AsyncMock(return_value={
            "user_name": "alice",
            "session_id": "sess-1",
        })
        expected = {"context": [{"role": "user", "content": "Hello"}]}

        with patch(PATCH_MC) as mock_mc:
            mock_mc.get_chat_context_by_session_id = AsyncMock(return_value=expected)
            result = await get_chat_context_by_session_id(request)

        assert result == expected
        mock_mc.get_chat_context_by_session_id.assert_called_once()


class TestUpdateChatContextBySessionId:
    @pytest.mark.asyncio
    async def test_delegates_with_all_fields(self):
        from models.memory import ChatContext

        chat_ctx = ChatContext(
            memory_id="m-1",
            user_name="alice",
            session_id="sess-1",
        )
        request = MagicMock()
        request.json = AsyncMock(return_value={
            "user_name": "alice",
            "session_id": "sess-1",
            "user_input": "What's the weather?",
            "agent_response": "It's sunny.",
            "chat_context": chat_ctx.model_dump(),
        })
        expected = {"success": True}

        with patch(PATCH_MC) as mock_mc:
            mock_mc.update_chat_context_by_session_id = AsyncMock(return_value=expected)
            result = await update_chat_context_by_session_id(request)

        assert result == expected
        call_arg = mock_mc.update_chat_context_by_session_id.call_args[0][0]
        assert call_arg.agent_response == "It's sunny."


class TestDeleteChatContextBySessionId:
    @pytest.mark.asyncio
    async def test_delegates_to_memory_center(self):
        request = MagicMock()
        request.json = AsyncMock(return_value={
            "user_name": "alice",
            "session_id": "sess-1",
        })
        expected = {"success": True}

        with patch(PATCH_MC) as mock_mc:
            mock_mc.delete_chat_context_by_session_id = AsyncMock(return_value=expected)
            result = await delete_chat_context_by_session_id(request)

        assert result == expected
        mock_mc.delete_chat_context_by_session_id.assert_called_once()
