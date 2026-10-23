from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field

from common.dto.base import FrozenDTO, stored_conversation_id


class InternalDomainEvent(FrozenDTO):
    """Event carried on the internal eventing stream.

    Older instances and retained stream entries only know ``room_id``, so the
    payload keeps emitting ``room_id`` while readers accept either key.
    """

    model_config = ConfigDict(serialize_by_alias=True)

    event_type: str = "internal"
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class MessageCommitted(InternalDomainEvent):
    event_type: Literal["message_committed"] = "message_committed"
    conversation_id: str = stored_conversation_id()
    message_id: str
    message_type: Literal["user", "agent"]
    agent_id: str | None = None
    room_agent_set: dict[str, str] | None = None
    agent_name: str | None = None
    was_successful: bool | None = None


class RunStateChanged(InternalDomainEvent):
    event_type: Literal["run_state_changed"] = "run_state_changed"
    run_id: str
    conversation_id: str = stored_conversation_id()
    old_state: str
    new_state: str


__all__ = [
    "InternalDomainEvent",
    "MessageCommitted",
    "RunStateChanged",
]
