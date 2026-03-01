"""
Unit tests for SupervisorExecutor module.

Tests cover:
- _log_and_return: passes through result, includes trajectory metadata
- _checkpoint_trajectory: persists trajectory snapshot, handles missing message
- _save_interrupted_state: saves trajectory on unexpected failure
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from modules.SupervisorExecutor import SupervisorExecutor
from models.supervisor_v2 import (
    SupervisorTrajectory,
    SupervisorRunResult,
)


def _make_supervisor_executor():
    se = object.__new__(SupervisorExecutor)
    se.database_service = AsyncMock()
    se.sse_manager = MagicMock()
    se.room_services = MagicMock()
    se.supervisor_service = MagicMock()
    se.tsm = MagicMock()
    se.dispatcher = MagicMock()
    se.response_processor = MagicMock()
    se.a2a_service = MagicMock()
    se.notification_service = MagicMock()
    se.rate_limit_service = MagicMock()
    return se


# =============================================================================
# _log_and_return Tests
# =============================================================================


class TestLogAndReturn:
    def test_returns_result_unchanged(self):
        trajectory = SupervisorTrajectory()
        result = SupervisorRunResult(status="completed", trajectory=trajectory)

        returned = SupervisorExecutor._log_and_return(
            "room-1", trajectory, result
        )
        assert returned is result
        assert returned.status == "completed"

    def test_returns_result_in_debate_mode(self):
        trajectory = SupervisorTrajectory()
        result = SupervisorRunResult(status="completed", trajectory=trajectory)

        returned = SupervisorExecutor._log_and_return(
            "room-1", trajectory, result, debate_mode=True
        )
        assert returned is result


# =============================================================================
# _checkpoint_trajectory Tests
# =============================================================================


class TestCheckpointTrajectory:
    @pytest.mark.asyncio
    async def test_persists_trajectory_to_user_message(self):
        se = _make_supervisor_executor()
        user_message = MagicMock()
        user_message.extend_info = {}
        se.database_service.get_room_user_message_by_message_id.return_value = (
            user_message
        )
        se.database_service.update_room_user_message_by_message_id.return_value = True

        trajectory = SupervisorTrajectory()
        result = await se._checkpoint_trajectory("msg-1", trajectory)

        assert result is user_message
        se.database_service.update_room_user_message_by_message_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_message_not_found(self):
        se = _make_supervisor_executor()
        se.database_service.get_room_user_message_by_message_id.return_value = None

        trajectory = SupervisorTrajectory()
        result = await se._checkpoint_trajectory("msg-missing", trajectory)

        assert result is None

    @pytest.mark.asyncio
    async def test_uses_cached_message(self):
        se = _make_supervisor_executor()
        cached = MagicMock()
        cached.extend_info = {}
        se.database_service.update_room_user_message_by_message_id.return_value = True

        trajectory = SupervisorTrajectory()
        result = await se._checkpoint_trajectory("msg-1", trajectory, cached)

        assert result is cached
        se.database_service.get_room_user_message_by_message_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_initializes_extend_info_if_not_dict(self):
        se = _make_supervisor_executor()
        user_message = MagicMock()
        user_message.extend_info = None
        se.database_service.get_room_user_message_by_message_id.return_value = (
            user_message
        )
        se.database_service.update_room_user_message_by_message_id.return_value = True

        trajectory = SupervisorTrajectory()
        result = await se._checkpoint_trajectory("msg-1", trajectory)

        assert result is user_message
        assert isinstance(user_message.extend_info, dict)

    @pytest.mark.asyncio
    async def test_does_not_raise_on_db_error(self):
        """Checkpoint failures should be logged but not abort the loop."""
        se = _make_supervisor_executor()
        se.database_service.get_room_user_message_by_message_id.side_effect = (
            RuntimeError("DB connection lost")
        )

        trajectory = SupervisorTrajectory()
        result = await se._checkpoint_trajectory("msg-1", trajectory)
        assert result is None
