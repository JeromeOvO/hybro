from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class InternalAgentMessage(FrozenDTO):
    agent_id: str
    role: str
    parts: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentTaskResult(FrozenDTO):
    task_id: str
    agent_id: str
    status: str
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AgentStreamEvent(FrozenDTO):
    task_id: str
    agent_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    final: bool = False


__all__ = [
    "AgentStreamEvent",
    "AgentTaskResult",
    "InternalAgentMessage",
]
