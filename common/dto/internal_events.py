from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from common.dto.base import FrozenDTO


class InternalDomainEvent(FrozenDTO):
    event_type: str = "internal"
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentRegistered(InternalDomainEvent):
    event_type: Literal["agent_registered"] = "agent_registered"
    agent_id: str


class RoomCreated(InternalDomainEvent):
    event_type: Literal["room_created"] = "room_created"
    room_id: str
    owner_id: str


class MessageCommitted(InternalDomainEvent):
    event_type: Literal["message_committed"] = "message_committed"
    room_id: str
    message_id: str


class RunStateChanged(InternalDomainEvent):
    event_type: Literal["run_state_changed"] = "run_state_changed"
    run_id: str
    room_id: str
    state: str


class HubAgentResponseInternal(InternalDomainEvent):
    event_type: Literal["hub_agent_response"] = "hub_agent_response"
    hub_id: str
    agent_id: str
    task_id: str | None = None
    response: dict[str, Any] = Field(default_factory=dict)


InternalEvent = Annotated[
    AgentRegistered
    | RoomCreated
    | MessageCommitted
    | RunStateChanged
    | HubAgentResponseInternal,
    Field(discriminator="event_type"),
]


__all__ = [
    "AgentRegistered",
    "HubAgentResponseInternal",
    "InternalDomainEvent",
    "InternalEvent",
    "MessageCommitted",
    "RoomCreated",
    "RunStateChanged",
]
