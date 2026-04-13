import pytest
from unittest.mock import AsyncMock, MagicMock


class TestSlotLifecycleEndToEnd:
    """Validate the full slot lifecycle flow from open to terminate."""

    @pytest.mark.asyncio
    async def test_full_agent_slot_lifecycle(self):
        """slot_opened -> slot_delta -> slot_snapshot -> slot_terminated(completed)"""
        from services.slot_lifecycle import SlotLifecycleManager
        from services.turn_event_service import TurnEventAppender

        mock_appender = MagicMock(spec=TurnEventAppender)
        mock_appender.append = AsyncMock(return_value=MagicMock())

        mock_redis = MagicMock()
        mock_redis.set_nx = AsyncMock(return_value=True)

        lifecycle = SlotLifecycleManager(mock_appender, mock_redis)

        # 1. Open
        await lifecycle.open_slot(
            room_id="room_1",
            turn_id="turn_1",
            slot_id="msg_1",
            slot_type="agent",
            agent_id="agent_1",
            agent_name="Agent A",
        )

        # 2. Deltas (emitted directly via appender, not lifecycle)
        await mock_appender.append(
            "room_1", "turn_1", "slot_delta",
            {"slot_id": "msg_1", "text_delta": "Hello "},
            persist=False,
        )
        await mock_appender.append(
            "room_1", "turn_1", "slot_delta",
            {"slot_id": "msg_1", "text_delta": "world"},
            persist=False,
        )

        # 3. Terminate with content
        await lifecycle.terminate_slot(
            room_id="room_1",
            turn_id="turn_1",
            slot_id="msg_1",
            status="completed",
            content="Hello world",
        )

        # Verify event sequence
        assert mock_appender.append.call_count == 5  # open + 2 deltas + snapshot + terminated
        event_types = [call.args[2] for call in mock_appender.append.call_args_list]
        assert event_types == [
            "slot_opened",
            "slot_delta",
            "slot_delta",
            "slot_snapshot",
            "slot_terminated",
        ]

    @pytest.mark.asyncio
    async def test_idempotent_termination(self):
        """Second terminate_slot call should be no-op."""
        from services.slot_lifecycle import SlotLifecycleManager

        mock_appender = MagicMock()
        mock_appender.append = AsyncMock(return_value=MagicMock())

        mock_redis = MagicMock()
        # First call: acquired, second call: not acquired
        mock_redis.set_nx = AsyncMock(side_effect=[True, False])

        lifecycle = SlotLifecycleManager(mock_appender, mock_redis)

        await lifecycle.terminate_slot(
            "room_1", "turn_1", "msg_1", "completed", content="hi"
        )
        await lifecycle.terminate_slot(
            "room_1", "turn_1", "msg_1", "completed", content="hi"
        )

        # Only first call should emit events (snapshot + terminated)
        assert mock_appender.append.call_count == 2

    @pytest.mark.asyncio
    async def test_summary_slot_lifecycle(self):
        """Summary slot: opened(summary) -> terminated(completed, content)."""
        from services.slot_lifecycle import SlotLifecycleManager

        mock_appender = MagicMock()
        mock_appender.append = AsyncMock(return_value=MagicMock())

        mock_redis = MagicMock()
        mock_redis.set_nx = AsyncMock(return_value=True)

        lifecycle = SlotLifecycleManager(mock_appender, mock_redis)

        await lifecycle.open_slot(
            room_id="room_1",
            turn_id="turn_1",
            slot_id="summary-user_msg_1",
            slot_type="summary",
            mode="supervisor",
        )
        await lifecycle.terminate_slot(
            room_id="room_1",
            turn_id="turn_1",
            slot_id="summary-user_msg_1",
            status="completed",
            content="Combined analysis shows...",
        )

        event_types = [call.args[2] for call in mock_appender.append.call_args_list]
        assert event_types == ["slot_opened", "slot_snapshot", "slot_terminated"]

    @pytest.mark.asyncio
    async def test_terminate_without_content_skips_snapshot(self):
        """slot_terminated without content should skip slot_snapshot."""
        from services.slot_lifecycle import SlotLifecycleManager

        mock_appender = MagicMock()
        mock_appender.append = AsyncMock(return_value=MagicMock())

        mock_redis = MagicMock()
        mock_redis.set_nx = AsyncMock(return_value=True)

        lifecycle = SlotLifecycleManager(mock_appender, mock_redis)

        await lifecycle.terminate_slot(
            "room_1", "turn_1", "msg_1", "canceled"
        )

        # Only slot_terminated, no snapshot
        assert mock_appender.append.call_count == 1
        event_types = [call.args[2] for call in mock_appender.append.call_args_list]
        assert event_types == ["slot_terminated"]

    @pytest.mark.asyncio
    async def test_failed_slot_with_error(self):
        """slot_terminated(failed) should include error in payload."""
        from services.slot_lifecycle import SlotLifecycleManager

        mock_appender = MagicMock()
        mock_appender.append = AsyncMock(return_value=MagicMock())

        mock_redis = MagicMock()
        mock_redis.set_nx = AsyncMock(return_value=True)

        lifecycle = SlotLifecycleManager(mock_appender, mock_redis)

        await lifecycle.terminate_slot(
            "room_1", "turn_1", "msg_1", "failed",
            error="agent_unavailable",
        )

        # Only slot_terminated (no snapshot since no content)
        assert mock_appender.append.call_count == 1
        call_args = mock_appender.append.call_args
        assert call_args.args[2] == "slot_terminated"
        assert call_args.args[3]["error"] == "agent_unavailable"
        assert call_args.args[3]["status"] == "failed"
