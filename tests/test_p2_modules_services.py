"""
Unit tests for modules/WorkflowCenter.py, modules/TaskCenter.py,
and services/agent_health_service.py.

Tests cover:
- WorkflowCenter: _success_response, _error_response, _get_first_text_from_task
- TaskCenter: delegation to task_service
- AgentHealthCheckService: _get_retry_delay backoff math, _update_agent_card_in_db partial update
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


# =============================================================================
# AgentHealthCheckService._update_agent_card_in_db Tests
# =============================================================================


class TestUpdateAgentCardInDb:
    """Verify that _update_agent_card_in_db uses a partial $set and never
    touches agent_card.url or agent_card.iconUrl."""

    @pytest.fixture
    def svc(self):
        from services.agent_health_service import AgentHealthService
        return AgentHealthService()

    @pytest.fixture
    def agent(self):
        """Minimal Agent stub with the fields _update_agent_card_in_db reads."""
        from unittest.mock import MagicMock
        agent = MagicMock()
        agent.agent_id = "agent-123"
        agent.agent_card.url = "https://registered.example.com"
        agent.agent_card.iconUrl = "https://s3.example.com/agent-avatars/agent-123/custom.png"
        return agent

    @pytest.fixture
    def fetched_card(self):
        from a2a.types import AgentCard, AgentCapabilities
        return AgentCard(
            name="Updated Name",
            description="New description",
            url="https://live-agent.example.com",  # would redirect if used
            version="2.0",
            capabilities=AgentCapabilities(),
            defaultInputModes=["text"],
            defaultOutputModes=["text"],
            skills=[],
            iconUrl="https://live-agent.example.com/icon.png",  # remote icon
        )

    @pytest.mark.asyncio
    async def test_partial_set_excludes_url_and_icon_url(self, svc, agent, fetched_card):
        """The $set dict must contain agent card fields but NEVER url or iconUrl."""
        captured_updates = {}

        async def fake_update_one(filter, update, *args, **kwargs):
            captured_updates.update(update.get("$set", {}))

        with patch("services.agent_health_service.mongodb") as mock_mongodb:
            mock_mongodb.agents_collection.update_one = AsyncMock(
                side_effect=fake_update_one
            )
            await svc._update_agent_card_in_db(agent, fetched_card)

        assert "agent_card.url" not in captured_updates, \
            "url must never be overwritten by health check"
        assert "agent_card.iconUrl" not in captured_updates, \
            "iconUrl (custom avatar) must never be overwritten by health check"

    @pytest.mark.asyncio
    async def test_partial_set_includes_expected_fields(self, svc, agent, fetched_card):
        """Core agent-card fields (name, description, version…) must be synced."""
        captured_updates = {}

        async def fake_update_one(filter, update, *args, **kwargs):
            captured_updates.update(update.get("$set", {}))

        with patch("services.agent_health_service.mongodb") as mock_mongodb:
            mock_mongodb.agents_collection.update_one = AsyncMock(
                side_effect=fake_update_one
            )
            await svc._update_agent_card_in_db(agent, fetched_card)

        assert captured_updates.get("agent_card.name") == "Updated Name"
        assert captured_updates.get("agent_card.description") == "New description"
        assert captured_updates.get("agent_card.version") == "2.0"

    @pytest.mark.asyncio
    async def test_db_update_uses_agent_id_filter(self, svc, agent, fetched_card):
        """update_one must be called with the correct agent_id filter."""
        with patch("services.agent_health_service.mongodb") as mock_mongodb:
            mock_mongodb.agents_collection.update_one = AsyncMock()
            await svc._update_agent_card_in_db(agent, fetched_card)

        call_args = mock_mongodb.agents_collection.update_one.call_args
        assert call_args[0][0] == {"agent_id": "agent-123"}

    @pytest.mark.asyncio
    async def test_db_exception_is_swallowed(self, svc, agent, fetched_card):
        """Errors from MongoDB must not propagate; they are only logged."""
        with patch("services.agent_health_service.mongodb") as mock_mongodb:
            mock_mongodb.agents_collection.update_one = AsyncMock(
                side_effect=Exception("DB down")
            )
            # Should not raise
            await svc._update_agent_card_in_db(agent, fetched_card)

    @pytest.mark.asyncio
    async def test_no_db_call_when_card_unchanged(self, svc, fetched_card):
        """If the stored card already matches the fetched card, no DB write should occur."""
        from unittest.mock import MagicMock

        # Build an agent whose stored agent_card is identical to fetched_card
        agent = MagicMock()
        agent.agent_id = "agent-123"
        agent.agent_card = fetched_card  # same object → all fields equal

        with patch("services.agent_health_service.mongodb") as mock_mongodb:
            mock_mongodb.agents_collection.update_one = AsyncMock()
            await svc._update_agent_card_in_db(agent, fetched_card)

        mock_mongodb.agents_collection.update_one.assert_not_called()
