"""
Unit tests for api/task.py endpoints.

Tests cover:
- legacy workflow task endpoints return HTTP 410
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from api.task import (
    get_all_sessions,
    get_base_task_by_session_id,
    get_meta_tasks_by_parent_task_id,
    query_base_task,
    query_task,
)


class TestLegacyTaskRoutes:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("handler", "argument"),
        [
            (query_task, "t-1"),
            (query_base_task, "t-1"),
            (get_all_sessions, "alice"),
            (get_base_task_by_session_id, "sess-1"),
            (get_meta_tasks_by_parent_task_id, "parent-1"),
        ],
    )
    async def test_returns_410_without_invoking_task_center(self, handler, argument):
        center = MagicMock()
        for method_name in (
            "query_all_sessions",
            "query_base_task_by_task_id",
            "query_base_tasks_by_session_id",
            "query_meta_task_by_task_id",
            "query_meta_tasks_by_parent_task_id",
        ):
            setattr(center, method_name, AsyncMock(return_value={"unexpected": True}))

        result = await handler(argument)

        assert result.status_code == 410
        assert center.mock_calls == []
