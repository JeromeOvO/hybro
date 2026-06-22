from datetime import UTC, datetime

import pytest

from common.dto import (
    RuntimeAgentGroup,
    RuntimeMessageContent,
    RuntimeRoomAgentMessage,
    RuntimeRoomMemory,
)
from common.dto.base import FrozenDict


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
