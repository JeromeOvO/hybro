"""
Unit tests for api/task.py endpoints.

Tests cover:
- query_task: validation and delegation
- query_base_task: delegation
- get_all_sessions: delegation
- get_base_task_by_session_id: delegation
- get_meta_tasks_by_parent_task_id: delegation
"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException

from api.task import (
    query_task,
    query_base_task,
    get_all_sessions,
    get_base_task_by_session_id,
    get_meta_tasks_by_parent_task_id,
)

PATCH_TC = "api.task.task_center"


class TestQueryTask:
    @pytest.mark.asyncio
    async def test_delegates_to_task_center(self):
        expected = {"task_id": "t-1", "status": "completed"}

        with patch(PATCH_TC) as mock_tc:
            mock_tc.query_meta_task_by_task_id = AsyncMock(return_value=expected)
            result = await query_task("t-1")

        assert result == expected
        call_arg = mock_tc.query_meta_task_by_task_id.call_args[0][0]
        assert call_arg.task_id == "t-1"


class TestQueryBaseTask:
    @pytest.mark.asyncio
    async def test_delegates_to_task_center(self):
        expected = {"task_id": "t-1", "type": "base"}

        with patch(PATCH_TC) as mock_tc:
            mock_tc.query_base_task_by_task_id = AsyncMock(return_value=expected)
            result = await query_base_task("t-1")

        assert result == expected
        call_arg = mock_tc.query_base_task_by_task_id.call_args[0][0]
        assert call_arg.task_id == "t-1"


class TestGetAllSessions:
    @pytest.mark.asyncio
    async def test_delegates_to_task_center(self):
        expected = [{"session_id": "s-1"}]

        with patch(PATCH_TC) as mock_tc:
            mock_tc.query_all_sessions = AsyncMock(return_value=expected)
            result = await get_all_sessions("alice")

        assert result == expected
        call_arg = mock_tc.query_all_sessions.call_args[0][0]
        assert call_arg.user_name == "alice"


class TestGetBaseTaskBySessionId:
    @pytest.mark.asyncio
    async def test_delegates_to_task_center(self):
        expected = [{"task_id": "t-1"}]

        with patch(PATCH_TC) as mock_tc:
            mock_tc.query_base_tasks_by_session_id = AsyncMock(return_value=expected)
            result = await get_base_task_by_session_id("sess-1")

        assert result == expected
        call_arg = mock_tc.query_base_tasks_by_session_id.call_args[0][0]
        assert call_arg.session_id == "sess-1"


class TestGetMetaTasksByParentTaskId:
    @pytest.mark.asyncio
    async def test_delegates_to_task_center(self):
        expected = [{"task_id": "meta-1"}]

        with patch(PATCH_TC) as mock_tc:
            mock_tc.query_meta_tasks_by_parent_task_id = AsyncMock(return_value=expected)
            result = await get_meta_tasks_by_parent_task_id("parent-1")

        assert result == expected
        call_arg = mock_tc.query_meta_tasks_by_parent_task_id.call_args[0][0]
        assert call_arg.parent_task_id == "parent-1"
