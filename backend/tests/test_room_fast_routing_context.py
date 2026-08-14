from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from context_memory import assembly
from models.memory import ConversationTurn, RoomMemory, TurnRole
from models.request import RoomCenterUserMessageRequest
from models.room import MessageContent, Room, RoomUserMessage
from models.room_services_models import ParseResult, ResolvedRoutingScope
from room.compat.runtime import RoomMessagePreflightContext, RoomServices


def _preflight_context(agent_count: int) -> RoomMessagePreflightContext:
    agent_set = {
        f"agent-{index}": f"Agent {index}" for index in range(1, agent_count + 1)
    }
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Route this request"),
    )
    request = RoomCenterUserMessageRequest(
        room_id="room-1",
        user_id="user-1",
        message=user_message,
    )
    room = Room(
        room_id="room-1",
        room_name="Fast room",
        room_owner_id="user-1",
        room_owner_name="User",
        room_agent_set=agent_set,
        extend_info={"use_supervisor": False, "debateMode": False},
    )
    return RoomMessagePreflightContext(
        request=request,
        target_group="room_team",
        mentioned_agent_ids=None,
        user_message=user_message,
        client_request_id=None,
        room=room,
        use_supervisor=False,
        message_text="Route this request",
        pre_resolved_mentions=None,
        pre_resolved_scope=ResolvedRoutingScope(
            selected_agent_set=agent_set,
            auto_assign_agents=False,
            agents=[],
        ),
        pre_resolved_selected_scope=None,
        token=SimpleNamespace(is_cancelled=False),
    )


def _service(
    room_memory: RoomMemory | None,
) -> tuple[RoomServices, AsyncMock, MagicMock]:
    memory_reader = AsyncMock(return_value=room_memory)
    service = RoomServices(
        room_store=SimpleNamespace(get_room_memory_by_room_id=memory_reader)
    )
    context_assembly = MagicMock()
    context_assembly.assemble_supervisor_context_from_memory.side_effect = (
        assembly.assemble_supervisor_context_from_memory
    )
    service.bind_context_memory(context_assembly=context_assembly)
    service.parse_user_message = AsyncMock(return_value=ParseResult(success=True))
    return service, memory_reader, context_assembly


@pytest.mark.asyncio
async def test_fast_multi_agent_routing_uses_recent_canonical_history():
    room_memory = RoomMemory(
        room_id="room-1",
        conversation_history=[
            ConversationTurn(role=TurnRole.USER, content=f"canonical turn {index}")
            for index in range(1, 7)
        ],
    )
    assert room_memory.memory_content is not None
    assert room_memory.memory_content.conversation_history == []
    service, memory_reader, context_assembly = _service(room_memory)

    response = await service._run_message_preflight_to_room(_preflight_context(2))

    assert response.success is True
    memory_reader.assert_awaited_once_with("room-1")
    parse_request = service.parse_user_message.await_args.kwargs["conversation_context"]
    assert "canonical turn 1" not in parse_request
    for index in range(2, 7):
        assert f"canonical turn {index}" in parse_request
    assert "User: Route this request" in parse_request
    assembly_call = context_assembly.assemble_supervisor_context_from_memory.call_args
    assert assembly_call.args[0] is room_memory
    assert assembly_call.kwargs["max_turns"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_count", "room_memory", "expected_memory_reads"),
    [
        (1, RoomMemory(room_id="room-1"), 0),
        (2, None, 1),
    ],
)
async def test_fast_routing_single_agent_or_no_memory_keeps_empty_context(
    agent_count: int,
    room_memory: RoomMemory | None,
    expected_memory_reads: int,
):
    service, memory_reader, context_assembly = _service(room_memory)

    response = await service._run_message_preflight_to_room(
        _preflight_context(agent_count)
    )

    assert response.success is True
    assert memory_reader.await_count == expected_memory_reads
    assert service.parse_user_message.await_args.kwargs["conversation_context"] is None
    context_assembly.assemble_supervisor_context_from_memory.assert_not_called()
