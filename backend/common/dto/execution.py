from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

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
    sender_id: str
    sender_name: str | None = None
    message: dict[str, Any] | None = None
    message_text: str | None = None
    attachments: list[dict[str, Any]] | None = None
    inline_file_ids: list[str] | None = None
    target_agent_ids: list[str] | None = None
    target_group: str | None = None
    target_group_id: str | None = None
    message_target_mode: str | None = None
    mentioned_agent_ids: list[str] | None = None
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
    room_id: str | None = None
    message_id: str | None = None
    dispatch_root_message_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    message: dict[str, Any] | None = None
    message_list: list[dict[str, Any]] | None = None
    scope_resolution_error: dict[str, Any] | None = None
    success: bool = True
    error: str | None = None
    status_code: int = 200
    should_start_orchestration: bool = True


class RunInfo(FrozenDTO):
    run_id: str
    room_id: str
    state: RunState | str
    trigger_message_id: str | None = None
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
    source: Literal["agent", "supervisor"]
    prompt: str
    message_id: str | None = None
    source_step_id: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    a2a_task_id: str | None = None
    a2a_context_id: str | None = None
    continuation_message_id: str | None = None
    display_message_id: str | None = None
    prompt_type: Literal["text", "choice", "confirmation"] = "text"
    choices: list[str] | None = None
    group_id: str | None = None
    group_total: int | None = None
    group_index: int | None = None
    status: Literal[
        "pending",
        "processing",
        "responded",
        "resolved",
        "expired",
        "canceled",
    ] = "pending"
    expires_at: datetime | None = None
    created_at: datetime | None = None
    user_input: str | None = None
    responded_at: datetime | None = None
    responded_by_user_id: str | None = None

    @model_validator(mode="after")
    def populate_message_id(self) -> "HITLRequest":
        if self.message_id is None:
            object.__setattr__(
                self,
                "message_id",
                self.display_message_id
                or self.continuation_message_id
                or self.user_message_id,
            )
        return self


class HITLResponse(FrozenDTO):
    request_id: str
    status: str = "ok"
    response_text: str | None = None
    responder_id: str | None = None
    resolved_at: datetime | None = None
    reclaimed: bool | None = None
    error: str | None = None


class AgentEvent(FrozenDTO):
    # Deprecated Common compatibility DTO. Phase 7b runtime-normalized agent
    # events live in execution.dispatch.agent_event.
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
