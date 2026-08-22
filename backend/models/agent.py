from datetime import datetime
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, field_serializer, field_validator

from common.types import AgentCard

# ---------------------------------------------------------------------------
# Agent-card field protection constants
#
# These frozensets define which AgentCard fields Hybro manages directly and
# must therefore never be overwritten by data arriving from external sources
# (hub syncs, health-check fetches, etc.).
#
# Two distinct sets exist because the semantics differ:
#
#   AGENT_CARD_HUB_NO_OVERWRITE — used when syncing data from a hub.
#     Hub agents own their complete card, including iconUrl.
#
#   AGENT_CARD_HEALTH_NO_SYNC — used when syncing from a live health-check.
#     The registered `url` is Hybro's source of truth (the live card may
#     advertise a different address), so only url is protected.
# ---------------------------------------------------------------------------

# Hub-sourced card fields are authoritative.
AGENT_CARD_HUB_NO_OVERWRITE: frozenset[str] = frozenset()

# Fields Hybro manages for health-check syncs; all others come from the live card.
AGENT_CARD_HEALTH_NO_SYNC: frozenset[str] = frozenset({"url"})


def coerce_legacy_agent_card(value: Any) -> Any:
    if value is None or isinstance(value, AgentCard) or isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value


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

    @field_validator("agent_card", mode="before")
    @classmethod
    def _coerce_agent_card(cls, value: Any) -> Any:
        return coerce_legacy_agent_card(value)

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

    # Origin: cloud (registered) or local (host-discovered).
    source: str = "cloud"
    local_agent_id: str | None = None

    # Derived at read time from Clerk — not persisted in MongoDB.
    provider_name: str | None = None

    # Fields excluded from DB serialization (populated at read time).
    _DB_EXCLUDE_FIELDS: ClassVar[set[str]] = {
        "provider_name",
    }

    def db_dump(self, **kwargs) -> dict:
        """Serialize for MongoDB, excluding derived fields."""
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
