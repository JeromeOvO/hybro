from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class AgentRoutingCandidate(FrozenDTO):
    agent_id: str
    name: str
    description: str = ""
    capabilities: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)


class RoomMessageSummary(FrozenDTO):
    agent_id: str | None = None
    agent_name: str
    message: str


class RoomMemoryGenerationInput(FrozenDTO):
    messages: list[RoomMessageSummary]
    existing_memory: str | None = None


__all__ = [
    "AgentRoutingCandidate",
    "RoomMemoryGenerationInput",
    "RoomMessageSummary",
]
