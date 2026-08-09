from datetime import UTC, datetime

import pytest

from common.dto import (
    RuntimeAgentGroup,
    RuntimeMessageContent,
    RuntimeRoomAgentMessage,
    RuntimeRoomMemory,
)
from common.dto.base import FrozenDict
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
        webhook_token_hash="hash",
        pending_continuation={"step": "resume"},
        turn_id="u1",
    )

    assert message.message_type == "agent"
    assert message.pending_continuation == {"step": "resume"}
    assert isinstance(message.pending_continuation, FrozenDict)
    with pytest.raises(TypeError):
        message.pending_continuation["step"] = "changed"


def test_runtime_room_memory_accepts_python_metadata_and_freezes_containers():
    created_at = datetime(2026, 6, 22, tzinfo=UTC)
    memory = RuntimeRoomMemory(
        room_id="r1",
        memory_id="m1",
        memory_content={"last_seen_at": created_at},
        conversation_history=[{"role": "user", "created_at": created_at}],
        room_summary={"last_updated_at": created_at, "key_decisions": ["ship"]},
        room_facts=[{"content": "fact", "created_at": created_at}],
        agent_success_history={
            "agent-1": {"last_called_at": created_at, "total_calls": 1}
        },
        extend_info={"checkpoint_at": created_at},
    )

    assert memory.room_summary["last_updated_at"] == created_at
    assert isinstance(memory.room_summary, FrozenDict)
    assert isinstance(memory.room_facts[0], FrozenDict)
    assert isinstance(memory.agent_success_history["agent-1"], FrozenDict)

    with pytest.raises(TypeError):
        memory.room_summary["last_updated_at"] = None
    with pytest.raises(TypeError):
        memory.room_summary["key_decisions"].append("delay")
    with pytest.raises(TypeError):
        memory.room_facts.append({"content": "other"})


def test_runtime_to_legacy_dump_omits_unset_defaults_and_preserves_explicit_none():
    from common.dto import RuntimeRoomMemory, RuntimeRoomRecord
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

    memory = RuntimeRoomMemory(room_id="r1", memory_id="mem-1")
    memory_payload = _dump_runtime(memory)

    assert memory_payload == {"room_id": "r1", "memory_id": "mem-1"}
    assert "conversation_history" not in memory_payload
    assert "room_summary" not in memory_payload
    assert "agent_success_history" not in memory_payload


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
        processing_message_id="u1",
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
        webhook_token_hash="hash",
        turn_id="u1",
    )

    runtime_room = room_to_runtime(room)
    runtime_content = message_content_to_runtime(message_content)
    runtime_message = room_agent_message_to_runtime(agent_message)

    assert runtime_room.room_agent_set == {"agent-1": "Agent One"}
    assert runtime_content.message_text == "hello"
    assert runtime_message.has_task_tracking is True
    assert runtime_message.webhook_token_hash == "hash"
    assert runtime_message.turn_id == "u1"
    assert runtime_to_room(runtime_room).room_id == "r1"
    assert runtime_to_message_content(runtime_content).message_text == "hello"
    assert runtime_to_room_agent_message(runtime_message).message_id == "a1"


def test_memory_conversion_preserves_summary():
    from dal.runtime_store.contracts import (
        room_memory_to_runtime,
        runtime_to_room_memory,
    )
    from models.memory import RoomMemory

    memory = RoomMemory(room_id="r1", memory_id="mem-1", total_messages=3)
    runtime_memory = room_memory_to_runtime(memory)

    assert runtime_memory.memory_id == "mem-1"
    assert runtime_memory.total_messages == 3
    assert runtime_to_room_memory(runtime_memory).memory_id == "mem-1"


def test_orchestration_run_document_conversion_round_trips_state_and_event():
    from dal.runtime_store.contracts import (
        orchestration_run_event_from_document,
        orchestration_run_event_to_document,
        orchestration_run_state_from_document,
        orchestration_run_state_to_document,
    )
    from models.orchestration import (
        OrchestrationEventType,
        OrchestrationRunEvent,
        OrchestrationRunState,
        OrchestrationStatus,
    )

    created_at = datetime(2026, 7, 5, tzinfo=UTC)
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="message-1",
        goal="Summarize room history",
        candidate_agent_ids=["agent-1"],
        client_request_id="client-1",
        status=OrchestrationStatus.RUNNING,
        state_version=2,
        created_at=created_at,
        updated_at=created_at,
    )
    event = OrchestrationRunEvent(
        event_id="event-1",
        run_id="run-1",
        room_id="room-1",
        type=OrchestrationEventType.STATE_REDUCED,
        state_version=2,
        payload={"status": "running"},
        created_at=created_at,
    )

    state_document = orchestration_run_state_to_document(state)
    event_document = orchestration_run_event_to_document(event)

    assert state_document["status"] == "running"
    assert event_document["type"] == "state_reduced"
    assert orchestration_run_state_from_document(state_document) == state
    assert orchestration_run_event_from_document(event_document) == event
