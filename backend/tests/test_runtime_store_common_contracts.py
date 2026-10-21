from datetime import UTC, datetime

import pytest

from common.dto import (
    RuntimeAgentGroup,
    RuntimeMessageContent,
    RuntimeRoomAgentMessage,
)
from common.types import AgentCapabilities, AgentCard


def test_runtime_agent_group_is_common_owned_and_immutable():
    group = RuntimeAgentGroup(
        group_id="g1",
        name="Researchers",
        type="user",
        owner_id="owner-1",
        agents=["agent-1"],
    )

    assert group.model_dump(mode="json")["agents"] == ["agent-1"]
    with pytest.raises(TypeError):
        group.agents.append("agent-2")


def test_runtime_agent_message_preserves_task_tracking_fields():
    created_at = datetime(2026, 6, 22, tzinfo=UTC)
    message = RuntimeRoomAgentMessage(
        room_id="r1",
        message_id="a1",
        agent_id="agent-1",
        message_created_at=created_at,
        message_content=RuntimeMessageContent(message_text="working"),
        has_task_tracking=True,
        turn_id="u1",
    )

    assert message.message_type == "agent"
    assert message.has_task_tracking is True
    assert message.turn_id == "u1"


def test_runtime_to_legacy_dump_omits_unset_defaults_and_preserves_explicit_none():
    from common.dto import RuntimeRoomRecord
    from dal.runtime_store.contracts import _dump_runtime, runtime_to_room

    room = RuntimeRoomRecord(
        room_id="r1",
        room_name="Renamed",
        room_owner_id="owner-1",
        room_owner_name="Owner",
    )
    room_payload = _dump_runtime(room)

    assert room_payload == {
        "room_id": "r1",
        "room_name": "Renamed",
        "room_owner_id": "owner-1",
        "room_owner_name": "Owner",
    }
    assert "room_agent_set" not in room_payload

    cleared_room = RuntimeRoomRecord(
        room_id="r1",
        room_name="Renamed",
        room_owner_id="owner-1",
        room_owner_name="Owner",
        applied_from_group=None,
    )
    cleared_room_payload = _dump_runtime(cleared_room)

    assert cleared_room_payload["applied_from_group"] is None
    assert "room_agent_set" not in cleared_room_payload
    assert (
        runtime_to_room(cleared_room).model_dump(mode="json", exclude_unset=True)[
            "applied_from_group"
        ]
        is None
    )


def _agent_card() -> AgentCard:
    return AgentCard(
        name="Agent One",
        url="https://agent.example/.well-known/agent.json",
        version="1.0",
        capabilities=AgentCapabilities(),
        skills=[],
    )


def test_agent_group_conversion_round_trips_legacy_model():
    from dal.runtime_store.contracts import (
        agent_group_to_runtime,
        runtime_to_agent_group,
    )
    from models.agent_group import AgentGroup

    legacy = AgentGroup(
        group_id="g1",
        name="Researchers",
        description="Research team",
        type="user",
        owner_id="owner-1",
        agents=["agent-1"],
    )

    runtime = agent_group_to_runtime(legacy)
    restored = runtime_to_agent_group(runtime)

    assert runtime.group_id == "g1"
    assert runtime.agents == ["agent-1"]
    assert restored == legacy


def test_agent_conversion_preserves_agent_card_and_status_value():
    from dal.runtime_store.contracts import agent_to_runtime, runtime_to_agent
    from models.agent import Agent, AgentStatus

    legacy = Agent(
        agent_id="agent-1",
        agent_card=_agent_card(),
        agent_status=AgentStatus.inactive,
        call_count=7,
        source="hub",
        hub_id="hub-1",
    )

    runtime = agent_to_runtime(legacy)
    restored = runtime_to_agent(runtime)

    assert runtime.agent_card.name == "Agent One"
    assert runtime.agent_status == "inactive"
    assert restored.agent_status is AgentStatus.inactive
    assert restored.call_count == 7


def test_room_and_message_conversion_preserves_runtime_fields():
    from dal.runtime_store.contracts import (
        message_content_to_runtime,
        room_agent_message_to_runtime,
        room_to_runtime,
        runtime_to_message_content,
        runtime_to_room,
        runtime_to_room_agent_message,
    )
    from models.room import MessageContent, Room, RoomAgentMessage

    created_at = datetime(2026, 6, 22, tzinfo=UTC)
    room = Room(
        room_id="r1",
        room_name="Room",
        room_owner_id="owner-1",
        room_owner_name="Owner",
        room_agent_set={"agent-1": "Agent One"},
        last_activity_at=created_at,
        is_pinned=True,
        pin_order=2.0,
    )
    message_content = MessageContent(message_text="hello")
    agent_message = RoomAgentMessage(
        room_id="r1",
        message_id="a1",
        agent_id="agent-1",
        message_created_at=created_at,
        message_content=message_content,
        related_message_id="u1",
        has_task_tracking=True,
        turn_id="u1",
    )

    runtime_room = room_to_runtime(room)
    runtime_content = message_content_to_runtime(message_content)
    runtime_message = room_agent_message_to_runtime(agent_message)

    assert runtime_room.room_agent_set == {"agent-1": "Agent One"}
    assert runtime_room.last_activity_at == created_at
    assert runtime_room.is_pinned is True
    assert runtime_room.pin_order == 2.0
    assert runtime_content.message_text == "hello"
    assert runtime_message.has_task_tracking is True
    assert runtime_message.turn_id == "u1"
    restored_room = runtime_to_room(runtime_room)
    assert restored_room.room_id == "r1"
    assert restored_room.last_activity_at == created_at
    assert restored_room.is_pinned is True
    assert restored_room.pin_order == 2.0
    assert runtime_to_message_content(runtime_content).message_text == "hello"
    assert runtime_to_room_agent_message(runtime_message).message_id == "a1"
