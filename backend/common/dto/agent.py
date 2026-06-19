from pydantic import Field, JsonValue

from common.dto.base import FrozenDTO


class AgentInfo(FrozenDTO):
    agent_id: str
    name: str | None = None
    description: str | None = None
    url: str | None = None
    provider_id: str | None = None
    status: str = "active"
    capabilities: list[str] = Field(default_factory=list)
    source: str = "cloud"
    hub_id: str | None = None
    is_hub_online: bool | None = None
    is_public: bool = True
    public_url: str | None = None
    rate_limit_per_user_per_hour: int | None = None
    rate_limit_system_per_hour: int | None = None
    call_count: int = 0
    raw_card: dict[str, JsonValue] = Field(default_factory=dict)


class AgentCardSnapshot(FrozenDTO):
    agent_id: str
    url: str
    name: str | None = None
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    raw_card: dict[str, JsonValue] = Field(default_factory=dict)


class AgentMatchResult(FrozenDTO):
    agent_id: str
    score: float = 0.0
    reason: str | None = None
    agent: AgentInfo | None = None


class HubAgentDescriptor(FrozenDTO):
    hub_id: str
    agent_id: str
    name: str | None = None
    url: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    raw_card: dict[str, JsonValue] = Field(default_factory=dict)


class SyncedHubAgent(FrozenDTO):
    hub_id: str
    agent_id: str
    status: str = "active"
    is_online: bool = True
    descriptor: HubAgentDescriptor | None = None


__all__ = [
    "AgentCardSnapshot",
    "AgentInfo",
    "AgentMatchResult",
    "HubAgentDescriptor",
    "SyncedHubAgent",
]
