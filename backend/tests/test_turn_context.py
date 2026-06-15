"""Tests for ``execution.orchestration.turn_context`` (QUOTE_REPLY)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from execution.orchestration.turn_context import (
    TurnQuoteMissingError,
    load_turn_context,
)
from models.quote import QuotedSnippet
from models.room import MessageContent, RoomUserMessage


def _user_msg(**kwargs) -> RoomUserMessage:
    defaults = dict(
        room_id="r1",
        message_id="u1",
        message_type="user",
        user_id="usr",
        message_content=MessageContent(message_text="follow up"),
        extend_info=None,
        quote_id=None,
        quote=None,
    )
    defaults.update(kwargs)
    return RoomUserMessage(**defaults)


@pytest.mark.asyncio
async def test_load_turn_context_legacy_only():
    db = MagicMock()
    um = _user_msg(
        extend_info={"quoted_text": "  hello  ", "quoted_sender_name": "Agent A"}
    )
    tc = await load_turn_context(db, um)
    assert tc.quoted_text == "hello"
    assert tc.quote_id is None
    assert tc.message_text == "follow up"
    db.get_quoted_snippet_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_load_turn_context_snippet_wins():
    snippet = QuotedSnippet(
        quote_id="q1",
        room_id="r1",
        created_by_user_id="u",
        text="from db",
        source_message_id="m1",
        source_kind="agent",
        sender_display_name="Bob",
    )
    db = MagicMock()
    db.get_quoted_snippet_by_id = AsyncMock(return_value=snippet)
    um = _user_msg(
        quote_id="q1",
        extend_info={"quoted_text": "inline different", "quote_id": "q1"},
    )
    tc = await load_turn_context(db, um)
    assert tc.quoted_text == "from db"
    db.get_quoted_snippet_by_id.assert_awaited_once_with("q1")


@pytest.mark.asyncio
async def test_load_turn_context_missing_snippet_raises():
    db = MagicMock()
    db.get_quoted_snippet_by_id = AsyncMock(return_value=None)
    um = _user_msg(quote_id="missing", extend_info={})
    with pytest.raises(TurnQuoteMissingError):
        await load_turn_context(db, um)
