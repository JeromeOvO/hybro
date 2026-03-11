from datetime import datetime
from enum import Enum
from typing import ClassVar

from a2a.types import AgentCard
from pydantic import BaseModel, field_serializer


class AgentStatus(Enum):
    active = "active"
    inactive = "inactive"
    deleted = "deleted"


class IssueStatus(str, Enum):
    open = "open"
    resolved = "resolved"


class Agent(BaseModel):
    # Primary identification field
    agent_id: str

    # Provider (register user id) - None for legacy agents
    provider_id: str | None = None

    # Agent card
    agent_card: AgentCard

    # Normalized URL for duplicate detection (derived from agent_card.url)
    normalized_url: str | None = None

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

    # Hub Phase 2 additions (see HYBRO_HUB_DESIGN.md §5.1, §15)
    source: str = "cloud"
    hub_id: str | None = None
    local_agent_id: str | None = None

    # Derived at read time from the hub document — not persisted in MongoDB.
    hub_owner_id: str | None = None
    is_hub_online: bool = False

    # Fields excluded from DB serialization (populated at read time).
    _DB_EXCLUDE_FIELDS: ClassVar[set[str]] = {"hub_owner_id", "is_hub_online"}

    def db_dump(self, **kwargs) -> dict:
        """Serialize for MongoDB, excluding derived hub fields."""
        kwargs.setdefault("mode", "json")
        return self.model_dump(exclude=self._DB_EXCLUDE_FIELDS, **kwargs)

    @field_serializer("agent_status")
    def serialize_status(self, value: AgentStatus) -> str:
        """Convert Enum to string value for storage"""
        if value is None:
            return None
        return value.value if isinstance(value, AgentStatus) else value


class AgentCapabilityIssue(BaseModel):
    issue_id: str  # UUID
    agent_id: str
    error_message: str
    query_text: str  # What was asked (for context)
    room_id: str | None = None
    message_id: str | None = None
    status: IssueStatus = IssueStatus.open
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None  # provider_id who resolved it

    @field_serializer("status")
    def serialize_issue_status(self, value: IssueStatus) -> str:
        if value is None:
            return None
        return value.value if isinstance(value, IssueStatus) else value
