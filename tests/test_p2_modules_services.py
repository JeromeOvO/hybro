"""
Unit tests for modules/WorkflowCenter.py, modules/TaskCenter.py,
and services/agent_health_service.py.

Tests cover:
- WorkflowCenter: _success_response, _error_response, _get_first_text_from_task
- TaskCenter: delegation to task_service
- AgentHealthCheckService: _get_retry_delay backoff math
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# =============================================================================
# WorkflowCenter Tests
# =============================================================================


class TestWorkflowCenterHelpers:
    @pytest.fixture
    def wc(self):
        from modules.WorkflowCenter import WorkflowCenter
        return WorkflowCenter.__new__(WorkflowCenter)

    def test_success_response(self, wc):
        r = wc._success_response("task-1")
        assert r.success is True
        assert r.error is None
        assert r.status_code == 200
        assert r.task_id == "task-1"

    def test_error_response_default_500(self, wc):
        r = wc._error_response("task-1", "Something broke")
        assert r.success is False
        assert r.error == "Something broke"
        assert r.status_code == 500
        assert r.task_id == "task-1"

    def test_error_response_custom_status(self, wc):
        r = wc._error_response("task-1", "Not found", status_code=404)
        assert r.status_code == 404


class TestGetFirstTextFromTask:
    @pytest.fixture
    def wc(self):
        from modules.WorkflowCenter import WorkflowCenter
        return WorkflowCenter.__new__(WorkflowCenter)

    def test_extracts_text_from_valid_task(self, wc):
        part = MagicMock()
        part.root.kind = "text"
        part.root.text = "Hello world"

        msg = MagicMock()
        msg.parts = [part]

        base_task = MagicMock()
        base_task.task.history = [msg]

        result = wc._get_first_text_from_task(base_task)
        assert result == "Hello world"

    def test_returns_fallback_for_empty_history(self, wc):
        base_task = MagicMock()
        base_task.task.history = []
        assert wc._get_first_text_from_task(base_task) == "No task description available"

    def test_returns_fallback_for_none_task(self, wc):
        base_task = MagicMock()
        base_task.task = None
        assert wc._get_first_text_from_task(base_task) == "No task description available"

    def test_returns_custom_fallback(self, wc):
        base_task = MagicMock()
        base_task.task = None
        assert wc._get_first_text_from_task(base_task, "Custom") == "Custom"


# =============================================================================
# TaskCenter Tests (delegation)
# =============================================================================


class TestTaskCenterDelegation:
    @pytest.fixture
    def tc(self):
        from modules.TaskCenter import TaskCenter
        tc = TaskCenter.__new__(TaskCenter)
        tc.task_service = MagicMock()
        return tc

    @pytest.mark.asyncio
    async def test_create_new_session_delegates(self, tc):
        tc.task_service.create_new_session = AsyncMock(return_value="ok")
        req = MagicMock()
        result = await tc.create_new_session(req)
        assert result == "ok"
        tc.task_service.create_new_session.assert_called_once_with(req)

    @pytest.mark.asyncio
    async def test_query_all_sessions_delegates(self, tc):
        tc.task_service.query_all_sessions = AsyncMock(return_value="sessions")
        req = MagicMock()
        result = await tc.query_all_sessions(req)
        assert result == "sessions"
        tc.task_service.query_all_sessions.assert_called_once_with(req)

    @pytest.mark.asyncio
    async def test_delete_meta_task_delegates(self, tc):
        tc.task_service.delete_meta_task_by_task_id = AsyncMock(return_value="deleted")
        req = MagicMock()
        result = await tc.delete_meta_task_by_task_id(req)
        assert result == "deleted"


# =============================================================================
# AgentHealthCheckService Tests
# =============================================================================


class TestGetRetryDelay:
    @pytest.fixture
    def svc(self):
        from services.agent_health_service import AgentHealthService
        return AgentHealthService(
            initial_retry_delay=10.0,
            max_retry_delay=120.0,
            backoff_multiplier=2.0,
        )

    def test_first_failure_uses_initial_delay(self, svc):
        assert svc._get_retry_delay(1) == 10.0

    def test_second_failure_doubles(self, svc):
        assert svc._get_retry_delay(2) == 20.0

    def test_third_failure_quadruples(self, svc):
        assert svc._get_retry_delay(3) == 40.0

    def test_caps_at_max_delay(self, svc):
        assert svc._get_retry_delay(10) == 120.0

    def test_large_failure_count_still_caps(self, svc):
        assert svc._get_retry_delay(100) == 120.0
