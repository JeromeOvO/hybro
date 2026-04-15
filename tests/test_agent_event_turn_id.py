import pytest
from modules.agent_event import AgentEvent


def test_agent_event_accepts_turn_id():
    """AgentEvent must accept turn_id field."""
    event = AgentEvent(
        kind="response",
        message_id="msg_1",
        room_id="room_1",
        agent_id="agent_1",
        turn_id="turn_1",
    )
    assert event.turn_id == "turn_1"


def test_agent_event_turn_id_defaults_to_none():
    """AgentEvent without turn_id defaults to None (Phase 0 compatibility)."""
    event = AgentEvent(
        kind="response",
        message_id="msg_1",
        room_id="room_1",
        agent_id="agent_1",
    )
    assert event.turn_id is None
