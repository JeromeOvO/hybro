"""C8 step 4b-4: user-message persistence lives in conversation.message_sending."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from conversation.message_sending import RoomMessageSender
from conversation.runtime import ConversationRuntime


def test_runtime_keeps_only_the_persistence_port_methods():
    for name in (
        "_check_message_text_length",
        "_validate_send_message_request",
        "_materialize_room_quote",
        "_delete_uncommitted_quote",
        "_persist_user_message",
        "_resolve_and_apply_attachments",
    ):
        assert not hasattr(ConversationRuntime, name), name
    assert hasattr(ConversationRuntime, "persist_message_to_room")
    assert hasattr(ConversationRuntime, "get_idempotent_user_message")


def test_sender_refuses_work_before_the_facade_is_bound():
    sender = ConversationRuntime()._message_sender

    with pytest.raises(RuntimeError, match="bind_facade"):
        sender._require_facade()
    with pytest.raises(RuntimeError, match="user-message commit"):
        sender._require_user_message_commit()


@pytest.mark.asyncio
async def test_runtime_delegates_idempotent_replay(monkeypatch):
    sentinel = object()
    replay = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(RoomMessageSender, "get_idempotent_user_message", replay)

    result = await ConversationRuntime().get_idempotent_user_message(
        room_id="r1",
        client_request_id="c1",
        idempotency_fingerprint="fp",
        idempotency_fingerprint_version=1,
    )

    assert result is sentinel
    replay.assert_awaited_once_with(
        room_id="r1",
        client_request_id="c1",
        idempotency_fingerprint="fp",
        idempotency_fingerprint_version=1,
    )
