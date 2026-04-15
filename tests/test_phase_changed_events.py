# tests/test_phase_changed_events.py
"""Tests for SupervisorExecutor._emit_phase() — Phase 1c phase_changed events."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_appender():
    appender = MagicMock()
    appender.append = AsyncMock(return_value=MagicMock())
    return appender


@pytest.fixture
def supervisor_with_appender(mock_appender):
    """Build a SupervisorExecutor with real _emit_phase but mocked dependencies."""
    from modules.SupervisorExecutor import SupervisorExecutor

    executor = SupervisorExecutor.__new__(SupervisorExecutor)
    executor._turn_appender = mock_appender
    executor.sse_manager = MagicMock(
        send_processing_status=AsyncMock(),
    )
    executor.database_service = MagicMock()
    executor.room_services = MagicMock()
    return executor


class TestPhaseChangedEvents:
    """Tests call SupervisorExecutor._emit_phase() — the real helper.

    _emit_phase() is added next to every send_processing_status() call.
    Testing it directly validates the appender integration without needing
    to drive the full run() loop.
    """

    @pytest.mark.asyncio
    async def test_planning_phase_emitted(
        self, supervisor_with_appender, mock_appender,
    ):
        """_emit_phase("planning") must call appender.append with phase_changed."""
        executor = supervisor_with_appender

        await executor._emit_phase(
            room_id="room_1", turn_id="turn_1",
            phase={"name": "planning"},
        )

        mock_appender.append.assert_called_once()
        call = mock_appender.append.call_args
        assert call.args[0] == "room_1"
        assert call.args[1] == "turn_1"
        assert call.args[2] == "phase_changed"
        assert call.args[3]["phase"]["name"] == "planning"

    @pytest.mark.asyncio
    async def test_delegating_phase_includes_agent_names(
        self, supervisor_with_appender, mock_appender,
    ):
        """phase_changed(delegating) must include agent_names and count."""
        executor = supervisor_with_appender

        await executor._emit_phase(
            room_id="room_1", turn_id="turn_1",
            phase={"name": "delegating", "agent_names": ["Agent A", "Agent B"], "count": 2},
        )

        payload = mock_appender.append.call_args.args[3]
        assert payload["phase"]["agent_names"] == ["Agent A", "Agent B"]
        assert payload["phase"]["count"] == 2

    @pytest.mark.asyncio
    async def test_phase_changed_skipped_without_turn_id(
        self, supervisor_with_appender, mock_appender,
    ):
        """If turn_id is None (legacy message), _emit_phase is a no-op."""
        executor = supervisor_with_appender

        await executor._emit_phase(
            room_id="room_1", turn_id=None,
            phase={"name": "planning"},
        )

        mock_appender.append.assert_not_called()

    @pytest.mark.asyncio
    async def test_phase_changed_skipped_without_appender(
        self, mock_appender,
    ):
        """If appender not available (Phase 0 not deployed), _emit_phase is a no-op."""
        from modules.SupervisorExecutor import SupervisorExecutor

        executor = SupervisorExecutor.__new__(SupervisorExecutor)
        executor._turn_appender = None  # no appender

        await executor._emit_phase(
            room_id="room_1", turn_id="turn_1",
            phase={"name": "planning"},
        )

        mock_appender.append.assert_not_called()
