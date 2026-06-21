"""Tests for RoomMessageCenter._emit_deterministic_digest."""

from unittest.mock import AsyncMock

import pytest

from models.room import CoordinatorAgentId


@pytest.fixture
def rmc():
    from execution.orchestration.room_message_center import RoomMessageCenter

    center = RoomMessageCenter.__new__(RoomMessageCenter)
    center.delivery = AsyncMock()
    center.message_reader = AsyncMock()
    center.message_writer = AsyncMock()
    center.message_reader.get_room_user_message_by_message_id = AsyncMock(
        return_value=None
    )
    center.message_writer.upsert_room_agent_message = AsyncMock(return_value=True)
    return center


class TestEmitDeterministicDigest:
    @pytest.mark.asyncio
    async def test_persists_summary_with_deterministic_origin(self, rmc):
        await rmc._emit_deterministic_digest(
            room_id="room-1",
            user_message_id="msg-1",
            agent_count=3,
        )

        rmc.message_writer.upsert_room_agent_message.assert_awaited_once()
        saved = rmc.message_writer.upsert_room_agent_message.call_args[0][0]
        assert saved.message_id == "summary-msg-1"
        assert saved.agent_id == CoordinatorAgentId.SYSTEM_HYBRO
        assert saved.extend_info["summary_origin"] == "deterministic"
        assert "3 agents responded" in saved.message_content.model_dump_json()
        rmc.delivery.send_agent_response.assert_awaited_once()
