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
    payload: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None


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


__all__ = [
    "HubAgentStatus",
    "HubConnectionInfo",
    "HubDispatchCommand",
    "HubDispatchResult",
    "HubInfo",
    "RelayPayload",
]
