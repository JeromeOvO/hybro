from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class AgentRoutingCandidate(FrozenDTO):
    agent_id: str
    name: str
    description: str = ""
    capabilities: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)


__all__ = [
    "AgentRoutingCandidate",
]
