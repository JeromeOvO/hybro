from datetime import datetime
from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class HubConnectionInfo(FrozenDTO):
    hub_id: str
    owner_id: str
    is_online: bool
    connected_at: datetime | None = None
    last_seen_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HubAgentStatus(FrozenDTO):
    hub_id: str
    agent_id: str
    status: str
    is_online: bool | None = None
    last_seen_at: datetime | None = None


class RelayPayload(FrozenDTO):
    hub_id: str
    payload: dict[str, Any]
    task_id: str | None = None
    room_id: str | None = None


class HubDispatchCommand(FrozenDTO):
    hub_id: str
    agent_id: str
    local_agent_id: str | None = None
    room_id: str | None = None
    user_message_id: str | None = None
    agent_message_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None
    task_data: dict[str, Any] = Field(default_factory=dict)
    task_created_at: datetime | None = None
    task_updated_at: datetime | None = None


class HubCancelCommand(FrozenDTO):
    hub_id: str
    agent_message_id: str
    local_agent_id: str
    task_id: str | None = None


class HubReplyCommand(FrozenDTO):
    hub_id: str
    agent_message_id: str
    local_agent_id: str
    room_id: str
    reply_text: str
    task_id: str | None = None
    context_id: str | None = None


class OfflineHubFailureCommand(FrozenDTO):
    room_id: str | None = None
    agent_message_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    failed_task_status: dict[str, Any] = Field(default_factory=dict)
    error_text: str = "Agent is offline"
    sse_event: dict[str, Any] = Field(default_factory=dict)


class HubDispatchResult(FrozenDTO):
    hub_id: str
    accepted: bool
    task_id: str | None = None
    error: str | None = None


class HubInfo(FrozenDTO):
    hub_id: str
    owner_id: str
    name: str | None = None
    is_online: bool = False
    agent_count: int = 0
    active_agent_count: int = 0
    inactive_agent_count: int = 0


class HubAgentCounts(FrozenDTO):
    active: int = 0
    inactive: int = 0


__all__ = [
    "HubAgentStatus",
    "HubAgentCounts",
    "HubCancelCommand",
    "HubConnectionInfo",
    "HubDispatchCommand",
    "HubDispatchResult",
    "HubInfo",
    "HubReplyCommand",
    "OfflineHubFailureCommand",
    "RelayPayload",
]
