"""
Unit tests for AgentDispatcher module.

Tests cover:
- _extract_user_input: deep null-safe navigation
- _resolve_allowed_agent_ids: group normalization and merging
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from models.room import RoomAgentMessage
from execution.dispatch.agent_dispatcher import AgentDispatcher


def _make_dispatcher():
    """Create an AgentDispatcher with mocked dependencies."""
    d = object.__new__(AgentDispatcher)
    d.agent_resolver = MagicMock()
    d.database_service = MagicMock()
    d.tsm = MagicMock()
    return d


# =============================================================================
# _extract_user_input Tests
# =============================================================================


class TestExtractUserInput:
    """Tests for safe text extraction from deep message structure."""

    def test_returns_text_from_valid_message(self):
        part = MagicMock()
        part.root = MagicMock()
        part.root.text = "Hello world"

        history_entry = MagicMock()
        history_entry.parts = [part]

        task = MagicMock()
        task.history = [history_entry]

        content = MagicMock()
        content.message_task = task

        msg = MagicMock(spec=RoomAgentMessage)
        msg.message_content = content

        assert AgentDispatcher._extract_user_input(msg) == "Hello world"

    def test_returns_empty_when_content_is_none(self):
        msg = MagicMock(spec=RoomAgentMessage)
        msg.message_content = None
        assert AgentDispatcher._extract_user_input(msg) == ""

    def test_returns_empty_when_task_is_none(self):
        content = MagicMock()
        content.message_task = None
        msg = MagicMock(spec=RoomAgentMessage)
        msg.message_content = content
        assert AgentDispatcher._extract_user_input(msg) == ""

    def test_returns_empty_when_history_is_empty(self):
        task = MagicMock()
        task.history = []
        content = MagicMock()
        content.message_task = task
        msg = MagicMock(spec=RoomAgentMessage)
        msg.message_content = content
        assert AgentDispatcher._extract_user_input(msg) == ""

    def test_returns_empty_when_parts_empty(self):
        history_entry = MagicMock()
        history_entry.parts = []
        task = MagicMock()
        task.history = [history_entry]
        content = MagicMock()
        content.message_task = task
        msg = MagicMock(spec=RoomAgentMessage)
        msg.message_content = content
        assert AgentDispatcher._extract_user_input(msg) == ""

    def test_returns_empty_when_text_is_none(self):
        part = MagicMock()
        part.root = MagicMock()
        part.root.text = None
        history_entry = MagicMock()
        history_entry.parts = [part]
        task = MagicMock()
        task.history = [history_entry]
        content = MagicMock()
        content.message_task = task
        msg = MagicMock(spec=RoomAgentMessage)
        msg.message_content = content
        assert AgentDispatcher._extract_user_input(msg) == ""


# =============================================================================
# _resolve_allowed_agent_ids Tests
# =============================================================================


class TestResolveAllowedAgentIds:
    """Tests for agent ID resolution from extend_info."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_extend_info(self):
        d = _make_dispatcher()
        msg = MagicMock()
        msg.extend_info = None
        assert await d._resolve_allowed_agent_ids(msg) == []

    @pytest.mark.asyncio
    async def test_returns_allowed_ids_directly(self):
        d = _make_dispatcher()
        msg = MagicMock()
        msg.extend_info = {"allowed_agent_ids": ["a1", "a2"]}
        result = await d._resolve_allowed_agent_ids(msg)
        assert set(result) == {"a1", "a2"}

    @pytest.mark.asyncio
    async def test_merges_group_members(self):
        d = _make_dispatcher()
        group = MagicMock()
        group.agents = ["a3", "a4"]
        d.database_service.get_agent_group_by_id = AsyncMock(return_value=group)

        msg = MagicMock()
        msg.extend_info = {
            "allowed_agent_ids": ["a1"],
            "target_group": "grp-001",
        }
        result = await d._resolve_allowed_agent_ids(msg)
        assert set(result) == {"a1", "a3", "a4"}

    @pytest.mark.asyncio
    async def test_skips_builtin_groups(self):
        d = _make_dispatcher()
        d.database_service.get_agent_group_by_id = AsyncMock()

        msg = MagicMock()
        msg.extend_info = {"target_group": "all_agents"}
        await d._resolve_allowed_agent_ids(msg)
        d.database_service.get_agent_group_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_group_as_list(self):
        d = _make_dispatcher()
        g1 = MagicMock()
        g1.agents = ["a1"]
        g2 = MagicMock()
        g2.agents = ["a2"]
        d.database_service.get_agent_group_by_id = AsyncMock(side_effect=[g1, g2])

        msg = MagicMock()
        msg.extend_info = {"target_group": ["grp-1", "grp-2"]}
        result = await d._resolve_allowed_agent_ids(msg)
        assert set(result) == {"a1", "a2"}

    @pytest.mark.asyncio
    async def test_handles_group_load_error_gracefully(self):
        d = _make_dispatcher()
        d.database_service.get_agent_group_by_id = AsyncMock(
            side_effect=RuntimeError("DB down")
        )
        msg = MagicMock()
        msg.extend_info = {
            "allowed_agent_ids": ["a1"],
            "target_group": "grp-broken",
        }
        result = await d._resolve_allowed_agent_ids(msg)
        assert result == ["a1"]
