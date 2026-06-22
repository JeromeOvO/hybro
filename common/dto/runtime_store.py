from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from common.dto.base import FrozenDTO
from common.types import AgentCard, Task


class RuntimeAgentRecord(FrozenDTO):
    agent_id: str
    provider_id: str | None = None
    agent_card: AgentCard
    normalized_url: str | None = None
    public_url: str | None = None
    agent_status: str = "active"
    call_count: int = 0
    call_success_count: int = 0
    like_count: int = 0
    dislike_count: int = 0
    rate_limit_per_user_per_hour: int | None = None
    rate_limit_system_per_hour: int | None = None
    is_public: bool = True
    source: str = "cloud"
    hub_id: str | None = None
    local_agent_id: str | None = None
    hub_owner_id: str | None = None
    is_hub_online: bool = False
    provider_name: str | None = None


class RuntimeAgentGroup(FrozenDTO):
    group_id: str
    name: str
    description: str | None = None
    type: str
    owner_id: str | None = None
    agents: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RuntimeRoomRecord(FrozenDTO):
    room_id: str
    room_name: str
    room_owner_id: str
    room_owner_name: str
    room_agent_set: dict[str, str] = Field(default_factory=dict)
    room_created_at: datetime | None = None
    applied_from_group: str | None = None
    membership_origin: str | None = None
    membership_origin_status: str | None = None
    source_group_id: str | None = None
    source_group_name: str | None = None
    extend_info: Any | None = None
    processing_message_id: str | None = None


class RuntimeUserAttachment(FrozenDTO):
    file_id: str
    s3_key: str
    mime_type: str
    file_name: str
    size_bytes: int
    file_url: str | None = None


class RuntimeMessageContent(FrozenDTO):
    message_text: str | None = None
    message_task: Task | None = None
    attachments: list[RuntimeUserAttachment] | None = None
    content_summary: dict[str, Any] | None = None


class RuntimeRoomMessage(FrozenDTO):
    room_id: str
    message_id: str
    message_created_at: datetime | None = None
    message_type: str
    user_id: str | None = None
    agent_id: str | None = None
    parent_message_id: str | None = None
    run_id: str | None = None
    client_request_id: str | None = None
    related_message_id: str | None = None
    message_content: RuntimeMessageContent
    step_number: int | None = None
    total_steps: int | None = None
    task_updated_at: datetime | None = None
    task_content: str | None = None
    extend_info: Any | None = None


class RuntimeRoomUserMessage(RuntimeRoomMessage):
    message_type: Literal["user"] = "user"
    processing_claimed_at: datetime | None = None
    quote_id: str | None = None
    quote: dict[str, Any] | None = None


class RuntimeRoomAgentMessage(RuntimeRoomMessage):
    message_type: Literal["agent"] = "agent"
    webhook_token_hash: str | None = None
    pending_continuation: dict[str, Any] | None = None
    last_notified_state: str | None = None
    agent_url: str | None = None
    task_created_at: datetime | None = None
    has_task_tracking: bool = False
    turn_id: str | None = None


class RuntimeRoomMemory(FrozenDTO):
    room_id: str
    memory_id: str
    memory_content: dict[str, Any] | None = None
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)
    max_history_turns: int = 100
    room_summary: dict[str, Any] = Field(default_factory=dict)
    room_facts: list[dict[str, Any]] = Field(default_factory=list)
    agent_success_history: dict[str, dict[str, Any]] = Field(default_factory=dict)
    memory_created_at: datetime | None = None
    last_activity_at: datetime | None = None
    total_messages: int = 0
    total_compactions: int = 0
    extend_info: Any | None = None


class RuntimeChatContext(FrozenDTO):
    memory_id: str
    user_name: str
    session_id: str
    context_data: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    extend_info: Any | None = None


__all__ = [
    "RuntimeAgentGroup",
    "RuntimeAgentRecord",
    "RuntimeChatContext",
    "RuntimeMessageContent",
    "RuntimeRoomAgentMessage",
    "RuntimeRoomMemory",
    "RuntimeRoomMessage",
    "RuntimeRoomRecord",
    "RuntimeRoomUserMessage",
    "RuntimeUserAttachment",
]
