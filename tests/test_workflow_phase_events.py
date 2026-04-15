# tests/test_workflow_phase_events.py
"""Tests for WorkflowCenter._emit_workflow_phase() — Phase 1c workflow step events."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_appender():
    appender = MagicMock()
    appender.append = AsyncMock(return_value=MagicMock())
    return appender


class TestWorkflowStepPhaseEvents:
    @pytest.mark.asyncio
    async def test_workflow_step_emitted_at_each_step(self, mock_appender):
        """Drive WorkflowCenter._emit_workflow_phase() and verify appender calls."""
        from modules.WorkflowCenter import WorkflowCenter

        wc = WorkflowCenter.__new__(WorkflowCenter)
        wc._turn_appender = mock_appender
        wc.sse_manager = MagicMock()

        for i in range(3):
            await wc._emit_workflow_phase("room_1", "turn_1", {
                "name": "workflow_step",
                "current": i + 1,
                "total": 3,
                "step_name": f"Step {i + 1}",
            })

        assert mock_appender.append.call_count == 3
        # Verify first step
        first_payload = mock_appender.append.call_args_list[0].args[3]
        assert first_payload["phase"]["current"] == 1
        assert first_payload["phase"]["total"] == 3
        assert first_payload["phase"]["step_name"] == "Step 1"
        # Verify last step
        last_payload = mock_appender.append.call_args_list[2].args[3]
        assert last_payload["phase"]["current"] == 3
        assert last_payload["phase"]["step_name"] == "Step 3"

    @pytest.mark.asyncio
    async def test_workflow_phase_skipped_without_turn_id(self, mock_appender):
        """No event emitted when turn_id is None (legacy workflow)."""
        from modules.WorkflowCenter import WorkflowCenter

        wc = WorkflowCenter.__new__(WorkflowCenter)
        wc._turn_appender = mock_appender

        await wc._emit_workflow_phase("room_1", None, {"name": "workflow_step"})
        mock_appender.append.assert_not_called()
