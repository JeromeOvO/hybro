from enum import Enum

from a2a.types import AgentCard
from pydantic import BaseModel, field_serializer


class AgentStatus(Enum):
    active = "active"
    inactive = "inactive"
    deleted = "deleted"


class Agent(BaseModel):
    # Primary identification field
    agent_id: str

    # Provider (register user id) - None for legacy agents
    provider_id: str | None = None

    # Agent card
    agent_card: AgentCard

    # Public (masked) URL for the agent
    public_url: str | None = None

    # Agent status
    agent_status: AgentStatus = AgentStatus.active

    # Count for agent usage
    call_count: int = 0

    # Count for agent success usage
    call_success_count: int = 0

    # Like count from user
    like_count: int = 0

    # Dislike count from user
    dislike_count: int = 0

    # Rate limiting configuration
    # Maximum requests per user per hour (None = unlimited)
    rate_limit_per_user_per_hour: int | None = None

    # Maximum requests for entire system per hour (None = unlimited)
    rate_limit_system_per_hour: int | None = None

    # Visibility: True = public (everyone can see/use), False = private (owner only)
    is_public: bool = True

    @field_serializer("agent_status")
    def serialize_status(self, value: AgentStatus) -> str:
        """Convert Enum to string value for storage"""
        if value is None:
            return None
        return value.value if isinstance(value, AgentStatus) else value
