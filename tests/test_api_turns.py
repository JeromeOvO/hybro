import pytest
from unittest.mock import AsyncMock, MagicMock, patch

PATCH_DB = "api.turns.db_service"
PATCH_AUTH = "api.turns.get_current_user"


@pytest.fixture
def mock_user():
    from common.auth import ClerkUser

    return ClerkUser(
        user_id="user_1",
        session_id="sess_1",
        claims={"sub": "user_1"},
    )


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_room_by_room_id = AsyncMock(
        return_value={"room_id": "room_1", "room_owner_id": "user_1"}
    )
    db.get_recent_turns = AsyncMock(return_value=[])
    db.get_turns_summary = AsyncMock(return_value=[])
    db.get_turn_events = AsyncMock(return_value=None)
    return db


class TestGetRecentTurns:
    @pytest.mark.asyncio
    async def test_returns_recent_turns_with_flat_events(self, mock_user, mock_db):
        from api.turns import get_recent_turns

        mock_db.get_recent_turns = AsyncMock(
            return_value=[
                {
                    "turn_id": "t1",
                    "status": "completed",
                    "events": [
                        {
                            "event_id": "e1",
                            "seq": 1,
                            "ts": 100,
                            "type": "turn_started",
                            "payload": {
                                "user_input": {"text": "hi", "attachments": []}
                            },
                        },
                    ],
                },
            ]
        )
        with patch(PATCH_DB, mock_db), patch(PATCH_AUTH, return_value=mock_user):
            result = await get_recent_turns(
                room_id="room_1", limit=50, user=mock_user
            )
        assert len(result) == 1
        assert result[0]["turn_id"] == "t1"
        # Wire format must be FLAT — payload fields promoted to top level
        wire_event = result[0]["events"][0]
        assert "user_input" in wire_event
        assert "payload" not in wire_event

    @pytest.mark.asyncio
    async def test_returns_empty_for_new_room(self, mock_user, mock_db):
        from api.turns import get_recent_turns

        with patch(PATCH_DB, mock_db), patch(PATCH_AUTH, return_value=mock_user):
            result = await get_recent_turns(
                room_id="room_1", limit=50, user=mock_user
            )
        assert result == []


class TestGetTurnById:
    @pytest.mark.asyncio
    async def test_returns_turn_journal_with_flat_events(self, mock_user, mock_db):
        from api.turns import get_turn_by_id

        mock_db.get_turn_events = AsyncMock(
            return_value={
                "turn_id": "t1",
                "status": "completed",
                "events": [
                    {
                        "event_id": "e1",
                        "seq": 1,
                        "ts": 100,
                        "type": "turn_started",
                        "payload": {
                            "user_input": {"text": "hi", "attachments": []}
                        },
                    },
                ],
            }
        )
        with patch(PATCH_DB, mock_db), patch(PATCH_AUTH, return_value=mock_user):
            result = await get_turn_by_id(
                room_id="room_1", turn_id="t1", user=mock_user
            )
        assert result["turn_id"] == "t1"
        wire_event = result["events"][0]
        assert "user_input" in wire_event
        assert "payload" not in wire_event

    @pytest.mark.asyncio
    async def test_returns_404_for_missing_turn(self, mock_user, mock_db):
        from api.turns import get_turn_by_id
        from fastapi import HTTPException

        with patch(PATCH_DB, mock_db), patch(PATCH_AUTH, return_value=mock_user):
            with pytest.raises(HTTPException) as exc:
                await get_turn_by_id(
                    room_id="room_1", turn_id="nonexistent", user=mock_user
                )
            assert exc.value.status_code == 404
