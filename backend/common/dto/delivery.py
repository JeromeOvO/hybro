from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field

from common.dto.base import FrozenDTO


class DeliveryEmitStatus(StrEnum):
    DELIVERED = "delivered"
    ALREADY_DELIVERED = "already_delivered"
    IN_FLIGHT = "in_flight"
    DEDUPLICATED = "deduplicated"  # Legacy publisher/deduplicator compatibility.
    FAILED = "failed"


class DeliveryEnvelope(FrozenDTO):
    room_id: str
    event_type: str
    payload: dict[str, Any]
    event_id: str | None = None
    timestamp: datetime | None = None
    trace_id: str | None = None


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
    trace_id: str | None = None


class ProcessingStatusEvent(DeliveryEventBase):
    event_type: Literal["processing_status"] = "processing_status"
    message_id: str
    status: Literal[
        "queued",
        "processing",
        "awaiting_input",
        "completed",
        "failed",
        "canceled",
        "rejected",
        "rate_limited",
        "error",
    ]
    agent_id: str | None = None
    details: dict | None = None
    related_message_id: str | None = None
    client_request_id: str | None = None
    agents: list[dict] | None = None
    delivery_id: str | None = None


class RunEventNotification(DeliveryEventBase):
    event_type: Literal["run_event"] = "run_event"
    event_id: str
    delivery_id: str | None = None
    run_id: str
    seq: int
    run_event_type: str
    payload: dict = Field(default_factory=dict)
    correlation_id: str | None = None


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


class TaskSubmittedEvent(DeliveryEventBase):
    event_type: Literal["task_submitted"] = "task_submitted"
    message_id: str
    task_id: str
    agent_name: str
    agent_id: str | None = None
    status: str = "working"
    related_message_id: str | None = None
    created_at: str | None = None
    step_number: int | None = None
    total_steps: int | None = None
    task_content: str | None = None
    client_request_id: str | None = None


class TaskUpdateEvent(DeliveryEventBase):
    event_type: Literal["task_update"] = "task_update"
    message_id: str
    status: str
    delivery_id: str | None = None
    content: str | None = None
    error: str | None = None
    requires_input: bool = False
    requires_auth: bool = False
    status_message: str | None = None
    agent_name: str | None = None
    agent_id: str | None = None
    related_message_id: str | None = None
    created_at: str | None = None
    step_number: int | None = None
    total_steps: int | None = None
    task_content: str | None = None
    parts: list[dict] | None = None
    client_request_id: str | None = None


class ArtifactUpdateEvent(DeliveryEventBase):
    event_type: Literal["artifact_update"] = "artifact_update"
    message_id: str
    agent_id: str
    artifact: Any
    append: bool = False
    last_chunk: bool = False
    client_request_id: str | None = None


class ErrorEvent(DeliveryEventBase):
    event_type: Literal["error"] = "error"
    error: str
    error_type: str | None = None
    message_id: str | None = None
    agent_id: str | None = None
    retry_after_seconds: int | None = None
    user_requests_used: int | None = None
    user_requests_limit: int | None = None
    system_requests_used: int | None = None
    system_requests_limit: int | None = None
    client_request_id: str | None = None


class CancellationEvent(DeliveryEventBase):
    event_type: Literal["cancellation"] = "cancellation"
    message_id: str
    reason: str | None = None


class HITLRequestEvent(DeliveryEventBase):
    event_type: Literal["hitl_request"] = "hitl_request"
    request_id: str
    message_id: str
    source: str
    prompt: str
    prompt_type: str
    choices: list[str] | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    source_step_id: str | None = None
    interaction_id: str | None = None
    interaction_status: str | None = None
    application_status: str | None = None
    group_id: str | None = None
    group_total: int | None = None
    group_index: int | None = None
    related_message_id: str | None = None
    client_request_id: str | None = None


class HITLResolvedEvent(DeliveryEventBase):
    event_type: Literal["hitl_resolved"] = "hitl_resolved"
    request_id: str
    message_id: str
    source: str
    status: str = "resolved"
    interaction_id: str | None = None
    interaction_status: str | None = None
    application_status: str | None = None
    error_message: str | None = None
    related_message_id: str | None = None
    client_request_id: str | None = None


class HubAgentEvent(DeliveryEventBase):
    event_type: Literal["hub_agent_event"] = "hub_agent_event"
    hub_id: str
    agent_id: str
    message_id: str
    status: str
    partial: str | None = None


DeliveryEvent = Annotated[
    ProcessingStatusEvent
    | RunEventNotification
    | AgentMessagePartial
    | AgentMessageFinal
    | TaskSubmittedEvent
    | TaskUpdateEvent
    | ArtifactUpdateEvent
    | ErrorEvent
    | CancellationEvent
    | HITLRequestEvent
    | HITLResolvedEvent
    | HubAgentEvent,
    Field(discriminator="event_type"),
]


__all__ = [
    "AgentMessageFinal",
    "AgentMessagePartial",
    "ArtifactUpdateEvent",
    "CancellationEvent",
    "DeliveryEnvelope",
    "DeliveryEvent",
    "DeliveryEventBase",
    "ErrorEvent",
    "HITLRequestEvent",
    "HITLResolvedEvent",
    "HubAgentEvent",
    "NotificationPayload",
    "ProcessingStatusEvent",
    "RunEventNotification",
    "SSEEvent",
    "TaskSubmittedEvent",
    "TaskUpdateEvent",
]
