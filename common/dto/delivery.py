from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from common.dto.base import FrozenDTO


class DeliveryEnvelope(FrozenDTO):
    room_id: str
    event_type: str
    payload: dict[str, Any]
    event_id: str | None = None
    timestamp: datetime | None = None


class SSEEvent(FrozenDTO):
    event: str
    data: dict[str, Any]
    id: str | None = None
    retry: int | None = None


class NotificationPayload(FrozenDTO):
    room_id: str
    message: str
    title: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class DeliveryEventBase(FrozenDTO):
    room_id: str
    timestamp: datetime | None = None


class ProcessingStatusEvent(DeliveryEventBase):
    event_type: Literal["processing_status"] = "processing_status"
    message_id: str
    status: Literal["queued", "processing", "completed", "failed", "canceled"]
    agent_id: str | None = None
    details: dict | None = None
    client_request_id: str | None = None
    agents: list[dict] | None = None


class RunEventNotification(DeliveryEventBase):
    event_type: Literal["run_event"] = "run_event"
    event_id: str
    run_id: str
    seq: int
    run_event_type: str
    payload: dict = Field(default_factory=dict)


class AgentMessagePartial(DeliveryEventBase):
    event_type: Literal["agent_message_partial"] = "agent_message_partial"
    message_id: str
    agent_id: str
    content_delta: str


class AgentMessageFinal(DeliveryEventBase):
    event_type: Literal["agent_message_final"] = "agent_message_final"
    message_id: str
    agent_id: str
    content: dict = Field(default_factory=dict)


class CancellationEvent(DeliveryEventBase):
    event_type: Literal["cancellation"] = "cancellation"
    message_id: str
    reason: str | None = None


class HITLRequestEvent(DeliveryEventBase):
    event_type: Literal["hitl_request"] = "hitl_request"
    request_id: str
    prompt: str
    prompt_type: str
    source: str
    message_id: str


class HITLResolvedEvent(DeliveryEventBase):
    event_type: Literal["hitl_resolved"] = "hitl_resolved"
    request_id: str
    message_id: str


class HubAgentEvent(DeliveryEventBase):
    event_type: Literal["hub_agent_event"] = "hub_agent_event"
    hub_id: str
    agent_id: str
    message_id: str
    status: str
    partial: str | None = None


class DebateRoundEvent(DeliveryEventBase):
    event_type: Literal["debate_round"] = "debate_round"
    round_number: int
    agent_id: str
    message_id: str


DeliveryEvent = Annotated[
    ProcessingStatusEvent
    | RunEventNotification
    | AgentMessagePartial
    | AgentMessageFinal
    | CancellationEvent
    | HITLRequestEvent
    | HITLResolvedEvent
    | HubAgentEvent
    | DebateRoundEvent,
    Field(discriminator="event_type"),
]


__all__ = [
    "AgentMessageFinal",
    "AgentMessagePartial",
    "CancellationEvent",
    "DebateRoundEvent",
    "DeliveryEnvelope",
    "DeliveryEvent",
    "DeliveryEventBase",
    "HITLRequestEvent",
    "HITLResolvedEvent",
    "HubAgentEvent",
    "NotificationPayload",
    "ProcessingStatusEvent",
    "RunEventNotification",
    "SSEEvent",
]
