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
    message_type: Literal["user", "agent"]
    agent_id: str | None = None
    room_agent_set: dict[str, str] | None = None
    agent_name: str | None = None
    was_successful: bool | None = None


class RunStateChanged(InternalDomainEvent):
    event_type: Literal["run_state_changed"] = "run_state_changed"
    run_id: str
    room_id: str
    old_state: str
    new_state: str


class HubAgentResponseInternal(InternalDomainEvent):
    event_type: Literal["hub_agent_response_internal"] = "hub_agent_response_internal"
    hub_id: str
    agent_id: str
    task_id: str
    room_id: str
    is_terminal: bool
    journal_id: str | None = None
    idempotency_key: str | None = None
    run_id: str | None = None
    claim_token: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


InternalEvent = Annotated[
    MessageCommitted | RunStateChanged | HubAgentResponseInternal,
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
