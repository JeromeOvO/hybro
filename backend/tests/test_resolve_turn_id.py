from unittest.mock import AsyncMock, MagicMock

import pytest

from models.room import RoomAgentMessage


def make_agent_msg(
    message_id: str,
    turn_id: str | None = None,
    related_message_id: str | None = None,
) -> RoomAgentMessage:
    return RoomAgentMessage(
        message_id=message_id,
        room_id="room_1",
        message_type="agent",
        message_content={"text": ""},
        sender_name="Agent",
        turn_id=turn_id,
        related_message_id=related_message_id,
    )


@pytest.fixture
def mock_db():
    return MagicMock()


class TestResolveTurnId:
    @pytest.mark.asyncio
    async def test_returns_persisted_turn_id(self, mock_db):
        from common.utils.turn_id import resolve_turn_id

        msg = make_agent_msg("msg_1", turn_id="turn_abc")
        result = await resolve_turn_id(msg, mock_db)
        assert result == "turn_abc"
        mock_db.get_room_user_message_by_message_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_walks_chain_to_user_message(self, mock_db):
        from common.utils.turn_id import resolve_turn_id

        msg = make_agent_msg("msg_3", related_message_id="msg_2")
        mock_db.get_room_user_message_by_message_id = AsyncMock(
            side_effect=[None, MagicMock()]  # msg_2 not user, msg_1 is user
        )
        mock_db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=make_agent_msg("msg_2", related_message_id="msg_1")
        )
        result = await resolve_turn_id(msg, mock_db)
        assert result == "msg_1"

    @pytest.mark.asyncio
    async def test_returns_message_id_when_no_related(self, mock_db):
        from common.utils.turn_id import resolve_turn_id

        msg = make_agent_msg("msg_orphan", related_message_id=None)
        result = await resolve_turn_id(msg, mock_db)
        assert result == "msg_orphan"

    @pytest.mark.asyncio
    async def test_stops_at_broken_chain(self, mock_db):
        from common.utils.turn_id import resolve_turn_id

        msg = make_agent_msg("msg_2", related_message_id="msg_1")
        mock_db.get_room_user_message_by_message_id = AsyncMock(return_value=None)
        mock_db.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
        result = await resolve_turn_id(msg, mock_db)
        assert result == "msg_1"
