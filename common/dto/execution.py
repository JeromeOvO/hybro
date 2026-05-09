from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from common.dto.base import FrozenDTO

RunState = Literal[
    "queued",
    "processing",
    "awaiting_input",
    "completed",
    "failed",
    "canceled",
]


class ExecutionRequest(FrozenDTO):
    room_id: str
    message_text: str
    sender_id: str
    sender_name: str | None = None
    client_request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(FrozenDTO):
    success: bool
    run_id: str | None = None
    message_id: str | None = None
    error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowState(FrozenDTO):
    run_id: str
    room_id: str
    state: str
    updated_at: datetime
    current_agent_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionAck(FrozenDTO):
    accepted: bool
    run_id: str | None = None
    message_id: str | None = None


class RunInfo(FrozenDTO):
    run_id: str
    room_id: str
    state: RunState
    created_at: datetime | None = None
    updated_at: datetime | None = None
    error: str | None = None


class HITLRequest(FrozenDTO):
    request_id: str
    room_id: str
    run_id: str | None = None
    prompt: str
    options: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HITLResponse(FrozenDTO):
    request_id: str
    response: str | dict[str, Any]
    user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentEvent(FrozenDTO):
    room_id: str
    agent_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime | None = None


__all__ = [
    "AgentEvent",
    "ExecutionAck",
    "ExecutionRequest",
    "ExecutionResult",
    "HITLRequest",
    "HITLResponse",
    "RunInfo",
    "RunState",
    "WorkflowState",
]
