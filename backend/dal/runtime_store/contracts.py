from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from common.dto import (
    RuntimeAgentGroup,
    RuntimeAgentRecord,
    RuntimeChatContext,
    RuntimeMessageContent,
    RuntimeRoomAgentMessage,
    RuntimeRoomMemory,
    RuntimeRoomRecord,
    RuntimeRoomUserMessage,
)
from models.agent import Agent
from models.agent_group import AgentGroup
from models.memory import ChatContext, RoomMemory
from models.orchestration import OrchestrationRunEvent, OrchestrationRunState
from models.room import MessageContent, Room, RoomAgentMessage, RoomUserMessage


def _dump_model(value: BaseModel) -> dict:
    return value.model_dump(mode="json")


def _dump_runtime(value: BaseModel) -> dict:
    return value.model_dump(mode="json", exclude_unset=True)


def agent_to_runtime(agent: Agent) -> RuntimeAgentRecord:
    return RuntimeAgentRecord.model_validate(_dump_model(agent))


def runtime_to_agent(agent: RuntimeAgentRecord) -> Agent:
    return Agent.model_validate(_dump_runtime(agent))


def agent_group_to_runtime(agent_group: AgentGroup) -> RuntimeAgentGroup:
    return RuntimeAgentGroup.model_validate(_dump_model(agent_group))


def runtime_to_agent_group(agent_group: RuntimeAgentGroup) -> AgentGroup:
    return AgentGroup.model_validate(_dump_runtime(agent_group))


def room_to_runtime(room: Room) -> RuntimeRoomRecord:
    return RuntimeRoomRecord.model_validate(_dump_model(room))


def runtime_to_room(room: RuntimeRoomRecord) -> Room:
    return Room.model_validate(_dump_runtime(room))


def message_content_to_runtime(message_content: MessageContent) -> RuntimeMessageContent:
    return RuntimeMessageContent.model_validate(_dump_model(message_content))


def runtime_to_message_content(
    message_content: RuntimeMessageContent,
) -> MessageContent:
    return MessageContent.model_validate(_dump_runtime(message_content))


def room_user_message_to_runtime(
    room_user_message: RoomUserMessage,
) -> RuntimeRoomUserMessage:
    return RuntimeRoomUserMessage.model_validate(_dump_model(room_user_message))


def runtime_to_room_user_message(
    room_user_message: RuntimeRoomUserMessage,
) -> RoomUserMessage:
    return RoomUserMessage.model_validate(_dump_runtime(room_user_message))


def room_agent_message_to_runtime(
    room_agent_message: RoomAgentMessage,
) -> RuntimeRoomAgentMessage:
    return RuntimeRoomAgentMessage.model_validate(_dump_model(room_agent_message))


def runtime_to_room_agent_message(
    room_agent_message: RuntimeRoomAgentMessage,
) -> RoomAgentMessage:
    return RoomAgentMessage.model_validate(_dump_runtime(room_agent_message))


def room_memory_to_runtime(room_memory: RoomMemory) -> RuntimeRoomMemory:
    return RuntimeRoomMemory.model_validate(_dump_model(room_memory))


def runtime_to_room_memory(room_memory: RuntimeRoomMemory) -> RoomMemory:
    return RoomMemory.model_validate(_dump_runtime(room_memory))


def chat_context_to_runtime(chat_context: ChatContext) -> RuntimeChatContext:
    return RuntimeChatContext.model_validate(_dump_model(chat_context))


def runtime_to_chat_context(chat_context: RuntimeChatContext) -> ChatContext:
    return ChatContext.model_validate(_dump_runtime(chat_context))


def orchestration_run_state_to_document(
    state: OrchestrationRunState,
) -> dict[str, Any]:
    return _dump_model(state)


def orchestration_run_state_from_document(
    document: dict[str, Any],
) -> OrchestrationRunState:
    return OrchestrationRunState.model_validate(document)


def orchestration_run_event_to_document(
    event: OrchestrationRunEvent,
) -> dict[str, Any]:
    return _dump_model(event)


def orchestration_run_event_from_document(
    document: dict[str, Any],
) -> OrchestrationRunEvent:
    return OrchestrationRunEvent.model_validate(document)


def runtime_agents(agents: Iterable[Agent]) -> list[RuntimeAgentRecord]:
    return [agent_to_runtime(agent) for agent in agents]


def runtime_agent_groups(
    agent_groups: Iterable[AgentGroup],
) -> list[RuntimeAgentGroup]:
    return [agent_group_to_runtime(agent_group) for agent_group in agent_groups]


def runtime_rooms(rooms: Iterable[Room]) -> list[RuntimeRoomRecord]:
    return [room_to_runtime(room) for room in rooms]


def runtime_user_messages(
    room_user_messages: Iterable[RoomUserMessage],
) -> list[RuntimeRoomUserMessage]:
    return [
        room_user_message_to_runtime(room_user_message)
        for room_user_message in room_user_messages
    ]


def runtime_agent_messages(
    room_agent_messages: Iterable[RoomAgentMessage],
) -> list[RuntimeRoomAgentMessage]:
    return [
        room_agent_message_to_runtime(room_agent_message)
        for room_agent_message in room_agent_messages
    ]


__all__ = [
    "_dump_model",
    "_dump_runtime",
    "agent_group_to_runtime",
    "agent_to_runtime",
    "chat_context_to_runtime",
    "message_content_to_runtime",
    "orchestration_run_event_from_document",
    "orchestration_run_event_to_document",
    "orchestration_run_state_from_document",
    "orchestration_run_state_to_document",
    "room_agent_message_to_runtime",
    "room_memory_to_runtime",
    "room_to_runtime",
    "room_user_message_to_runtime",
    "runtime_agent_groups",
    "runtime_agent_messages",
    "runtime_agents",
    "runtime_rooms",
    "runtime_to_agent",
    "runtime_to_agent_group",
    "runtime_to_chat_context",
    "runtime_to_message_content",
    "runtime_to_room",
    "runtime_to_room_agent_message",
    "runtime_to_room_memory",
    "runtime_to_room_user_message",
    "runtime_user_messages",
]
