import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_appender():
    appender = MagicMock()
    appender.append = AsyncMock(return_value=MagicMock())
    return appender


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.set_nx = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def slot_lifecycle(mock_appender, mock_redis):
    from services.slot_lifecycle import SlotLifecycleManager

    return SlotLifecycleManager(mock_appender, mock_redis)


class TestOpenSlot:
    @pytest.mark.asyncio
    async def test_open_slot_emits_event(self, slot_lifecycle, mock_appender):
        await slot_lifecycle.open_slot(
            room_id="room_1",
            turn_id="turn_1",
            slot_id="msg_1",
            slot_type="agent",
            agent_id="agent_1",
            agent_name="Agent A",
        )
        mock_appender.append.assert_called_once_with(
            "room_1", "turn_1", "slot_opened",
            {
                "slot_id": "msg_1",
                "slot_type": "agent",
                "agent_id": "agent_1",
                "agent_name": "Agent A",
            },
        )


class TestTerminateSlot:
    @pytest.mark.asyncio
    async def test_terminate_with_content_emits_snapshot_then_terminated(
        self, slot_lifecycle, mock_appender
    ):
        await slot_lifecycle.terminate_slot(
            room_id="room_1",
            turn_id="turn_1",
            slot_id="msg_1",
            status="completed",
            content="Hello world",
            artifacts=[{"type": "text"}],
        )
        assert mock_appender.append.call_count == 2
        first_call = mock_appender.append.call_args_list[0]
        assert first_call.args[2] == "slot_snapshot"
        second_call = mock_appender.append.call_args_list[1]
        assert second_call.args[2] == "slot_terminated"

    @pytest.mark.asyncio
    async def test_terminate_without_content_skips_snapshot(
        self, slot_lifecycle, mock_appender
    ):
        await slot_lifecycle.terminate_slot(
            room_id="room_1",
            turn_id="turn_1",
            slot_id="msg_1",
            status="failed",
            error="agent_unavailable",
        )
        assert mock_appender.append.call_count == 1
        call = mock_appender.append.call_args_list[0]
        assert call.args[2] == "slot_terminated"

    @pytest.mark.asyncio
    async def test_terminate_is_idempotent_via_redis(
        self, mock_appender, mock_redis
    ):
        from services.slot_lifecycle import SlotLifecycleManager

        mock_redis.set_nx = AsyncMock(return_value=False)  # already terminated
        lifecycle = SlotLifecycleManager(mock_appender, mock_redis)

        await lifecycle.terminate_slot(
            "room_1", "turn_1", "msg_1", "completed", content="hi"
        )
        mock_appender.append.assert_not_called()

    @pytest.mark.asyncio
    async def test_terminate_redis_key_uses_turn_and_slot(
        self, slot_lifecycle, mock_redis
    ):
        await slot_lifecycle.terminate_slot(
            "room_1", "turn_1", "msg_abc", "completed"
        )
        mock_redis.set_nx.assert_called_once_with(
            "slot_terminated:turn_1:msg_abc", "completed", ex=3600
        )
