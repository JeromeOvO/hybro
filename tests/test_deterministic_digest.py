"""Tests for RoomMessageCenter._emit_deterministic_digest."""

from unittest.mock import AsyncMock

import pytest

from models.room import CoordinatorAgentId


@pytest.fixture
def rmc():
    from execution.orchestration.room_message_center import RoomMessageCenter

    center = RoomMessageCenter.__new__(RoomMessageCenter)
    center.sse_manager = AsyncMock()
    center.database_service = AsyncMock()
    center._store = center.database_service
    center.database_service.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    center.database_service.upsert_room_agent_message = AsyncMock(return_value=True)
    return center


class TestEmitDeterministicDigest:
    @pytest.mark.asyncio
    async def test_persists_summary_with_deterministic_origin(self, rmc):
        await rmc._emit_deterministic_digest(
            room_id="room-1",
            user_message_id="msg-1",
            agent_count=3,
        )

        rmc.database_service.upsert_room_agent_message.assert_awaited_once()
        saved = rmc.database_service.upsert_room_agent_message.call_args[0][0]
        assert saved.message_id == "summary-msg-1"
        assert saved.agent_id == CoordinatorAgentId.SYSTEM_HYBRO
        assert saved.extend_info["summary_origin"] == "deterministic"
        assert "3 agents responded" in saved.message_content.model_dump_json()
        rmc.sse_manager.send_agent_response.assert_awaited_once()
