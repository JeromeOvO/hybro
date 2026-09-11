"""An explicit @mention is an instruction, not only a scope restriction.

Naming an Agent must reach the orchestrator as a stated fact about the turn, and
the editor's stored token must not. These cover the projection boundary: what the
durable message keeps versus what the model is shown.
"""

from types import SimpleNamespace

import pytest

from common.utils.context_utils import split_agent_mentions
from execution.orchestrator_routing import (
    RoomMessageEnvelopeResolver,
    _addressed_agents_note,
)

OPENQFR_ID = "d50ef066fba94612b2d1854b52c18559"
OTHER_ID = "aabbccddeeff00112233445566778899"


def _message(text: str, extend_info: dict | None = None):
    return SimpleNamespace(
        message_id="msg-1",
        room_id="room-1",
        message_content=SimpleNamespace(message_text=text, attachments=None),
        extend_info=extend_info or {},
    )


def _request(*, scope: dict, mode: str = "supervisor"):
    return SimpleNamespace(
        room_user_message_id="msg-1",
        room_id="room-1",
        user_id="user-1",
        mode=mode,
        agent_scope=scope,
        client_request_id="req-1",
    )


def _resolver(message, *, room_agents: list[str] | None = None):
    async def get_user_message(message_id):
        return message

    async def list_room_agent_ids(room_id):
        return room_agents or []

    return RoomMessageEnvelopeResolver(
        get_user_message=get_user_message,
        list_room_agent_ids=list_room_agent_ids,
    )


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------


def test_mention_token_is_removed_and_reported_separately():
    cleaned, mentions = split_agent_mentions(f"<@{OPENQFR_ID}|OpenQFR> hello there")

    assert cleaned == "hello there"
    assert mentions == [(OPENQFR_ID, "OpenQFR")]


def test_mention_removal_keeps_line_structure():
    cleaned, _ = split_agent_mentions(f"<@{OPENQFR_ID}|OpenQFR> first\nsecond")

    assert cleaned == "first\nsecond"


def test_message_of_only_a_mention_still_has_a_body():
    cleaned, mentions = split_agent_mentions(f"<@{OPENQFR_ID}|OpenQFR>")

    assert cleaned == "@OpenQFR"
    assert mentions == [(OPENQFR_ID, "OpenQFR")]


# ---------------------------------------------------------------------------
# Envelope projection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_addressed_agent_is_reported_and_token_stripped():
    raw = f"<@{OPENQFR_ID}|OpenQFR> hello, who are you?"
    envelope = await _resolver(_message(raw)).load_envelope(
        _request(scope={"source": "mention", "agent_ids": [OPENQFR_ID]})
    )

    # The durable text is untouched, so the stored message still renders a chip.
    assert envelope.message_text == raw
    # The model sees prose plus a stated fact, never the editor token.
    assert envelope.model_text == "hello, who are you?"
    assert OPENQFR_ID not in envelope.model_text
    assert envelope.addressed_agents == ("OpenQFR",)
    assert _addressed_agents_note(envelope.addressed_agents) == (
        "[Explicitly addressed by the user: OpenQFR]"
    )


@pytest.mark.asyncio
async def test_mention_outside_the_candidate_scope_is_not_reported_as_addressed():
    raw = f"<@{OTHER_ID}|Some Other Agent> do the thing"
    envelope = await _resolver(_message(raw)).load_envelope(
        _request(scope={"source": "mention", "agent_ids": [OPENQFR_ID]})
    )

    assert envelope.addressed_agents == ()
    assert "@" not in envelope.model_text


@pytest.mark.asyncio
async def test_message_without_mentions_is_passed_through():
    envelope = await _resolver(_message("just a question")).load_envelope(
        _request(scope={"source": "room_default"})
    )

    assert envelope.model_text == "just a question"
    assert envelope.addressed_agents == ()

    # A room-scoped turn still reports a mention of a room member.
    room = await _resolver(
        _message(f"<@{OPENQFR_ID}|OpenQFR> hi"), room_agents=[OPENQFR_ID]
    ).load_envelope(_request(scope={"source": "room_default"}))
    assert room.addressed_agents == ("OpenQFR",)
