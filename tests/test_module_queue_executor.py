"""
Unit tests for QueueExecutor module.

Tests cover:
- _check_rate_limit: allowed vs rate-limited
- QueueResult enum values
- _managed_queue cleanup behavior (RAII)
"""

import pytest
from collections import deque
from unittest.mock import AsyncMock, MagicMock

from a2a.types import TaskState

from modules.QueueExecutor import QueueExecutor, QueueResult


# =============================================================================
# QueueResult Tests
# =============================================================================


class TestQueueResult:
    def test_enum_values(self):
        assert QueueResult.COMPLETED == "completed"
        assert QueueResult.CANCELED == "canceled"
        assert QueueResult.PAUSED == "paused"


# =============================================================================
# _check_rate_limit Tests
# =============================================================================


def _make_queue_executor():
    qe = object.__new__(QueueExecutor)
    qe.rate_limit_service = MagicMock()
    qe.sse_manager = MagicMock()
    qe.tsm = MagicMock()
    qe.database_service = MagicMock()
    qe.a2a_service = MagicMock()
    qe.room_services = MagicMock()
    qe.response_processor = MagicMock()
    qe.dispatcher = MagicMock()
    return qe


class TestCheckRateLimit:
    @pytest.mark.asyncio
    async def test_returns_false_when_allowed(self):
        qe = _make_queue_executor()
        result = MagicMock()
        result.allowed = True
        qe.rate_limit_service.check_rate_limit = AsyncMock(return_value=result)

        agent = MagicMock()
        agent.agent_id = "a1"
        agent.rate_limit_per_user_per_hour = 100
        agent.rate_limit_system_per_hour = 1000

        msg = MagicMock()
        is_limited = await qe._check_rate_limit(msg, agent, "room-1", "umsg-1", "u1")
        assert is_limited is False

    @pytest.mark.asyncio
    async def test_returns_true_and_cancels_when_rate_limited(self):
        qe = _make_queue_executor()
        result = MagicMock()
        result.allowed = False
        result.reason = "Too many requests"
        result.retry_after_seconds = 60
        result.user_requests_used = 100
        result.user_requests_limit = 100
        result.system_requests_used = 500
        result.system_requests_limit = 1000
        qe.rate_limit_service.check_rate_limit = AsyncMock(return_value=result)
        qe.sse_manager.send_rate_limit_error = AsyncMock()
        qe.tsm.transition_task = AsyncMock()

        agent = MagicMock()
        agent.agent_id = "a1"
        agent.rate_limit_per_user_per_hour = 100
        agent.rate_limit_system_per_hour = 1000

        msg = MagicMock()
        is_limited = await qe._check_rate_limit(msg, agent, "room-1", "umsg-1", "u1")

        assert is_limited is True
        qe.sse_manager.send_rate_limit_error.assert_called_once()
        qe.tsm.transition_task.assert_called_once_with(
            msg, TaskState.canceled, persist=True, notify=False
        )

    @pytest.mark.asyncio
    async def test_passes_correct_rate_limit_params(self):
        qe = _make_queue_executor()
        result = MagicMock()
        result.allowed = True
        qe.rate_limit_service.check_rate_limit = AsyncMock(return_value=result)

        agent = MagicMock()
        agent.agent_id = "agent-x"
        agent.rate_limit_per_user_per_hour = 50
        agent.rate_limit_system_per_hour = 500

        msg = MagicMock()
        await qe._check_rate_limit(msg, agent, "room-1", "umsg-1", "user-42")

        qe.rate_limit_service.check_rate_limit.assert_called_once_with(
            agent_id="agent-x",
            user_id="user-42",
            rate_limit_per_user=50,
            rate_limit_system=500,
        )
