from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field

from common.dto.base import FrozenDTO


class RunState(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class ExecutionRequest(FrozenDTO):
    room_id: str
    message_text: str
    sender_id: str
    sender_name: str | None = None
    attachments: list[dict[str, Any]] | None = None
    target_agent_ids: list[str] | None = None
    parent_message_id: str | None = None
    client_request_id: str | None = None
    mode: Literal["direct", "supervisor", "debate"] = "direct"


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
    room_id: str
    message_id: str
    dispatch_root_message_id: str | None = None
    user_id: str
    user_name: str
    message: dict[str, Any] = Field(default_factory=dict)
    message_list: list[dict[str, Any]] | None = None
    scope_resolution_error: dict[str, Any] | None = None
    success: bool = True
    error: str | None = None
    status_code: int = 200


class RunInfo(FrozenDTO):
    run_id: str
    room_id: str
    state: RunState
    agent_id: str | None = None
    parent_run_id: str | None = None
    seq: int = 0
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    error: str | None = None


class HITLRequest(FrozenDTO):
    request_id: str
    room_id: str
    user_message_id: str
    prompt: str
    prompt_type: Literal["text", "confirmation"] = "text"
    source: Literal["agent", "supervisor"] = "agent"
    status: Literal["pending", "resolved", "expired", "canceled"] = "pending"
    agent_id: str | None = None
    a2a_task_id: str | None = None
    continuation_message_id: str | None = None
    created_at: datetime | None = None


class HITLResponse(FrozenDTO):
    request_id: str
    response_text: str
    responder_id: str
    resolved_at: datetime | None = None


class AgentEvent(FrozenDTO):
    room_id: str
    agent_id: str
    message_id: str
    event_type: Literal["partial", "final", "status_update", "error", "input_required"]
    payload: dict[str, Any] = Field(default_factory=dict)
    hub_id: str | None = None


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
