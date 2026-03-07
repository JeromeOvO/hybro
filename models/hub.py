"""Hub and Relay protocol models.

Defines the Hub registration model and the Pydantic schemas that form
the relay protocol contract between the cloud backend and hub daemons.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from common.utils.time import utcnow

# ---------------------------------------------------------------------------
# Hub registration
# ---------------------------------------------------------------------------

class Hub(BaseModel):
    hub_id: str
    user_id: str
    registered_at: datetime = Field(default_factory=utcnow)
    last_connected_at: datetime | None = None
    is_online: bool = False
    connection_token: str | None = None


# ---------------------------------------------------------------------------
# Relay-to-hub events (cloud -> hub, delivered via SSE)
# ---------------------------------------------------------------------------

class RelayToHubEvent(BaseModel):
    """Event pushed from cloud to hub via the SSE stream."""

    type: str  # "user_message" | "heartbeat"
    room_id: str | None = None
    user_message_id: str | None = None
    agent_message_id: str | None = None
    agent_id: str | None = None
    local_agent_id: str | None = None
    message: dict | None = None  # A2A Message payload


# ---------------------------------------------------------------------------
# Hub-to-relay events (hub -> cloud, via POST /publish)
# ---------------------------------------------------------------------------

class HubPublishEvent(BaseModel):
    """Single event in a publish batch from hub to cloud."""

    type: str  # "task_submitted" | "agent_token" | "agent_response" | "processing_status"
    agent_message_id: str
    data: dict


class HubPublishRequest(BaseModel):
    """POST /relay/hub/{hub_id}/publish body."""

    room_id: str
    events: list[HubPublishEvent]


# ---------------------------------------------------------------------------
# Agent sync
# ---------------------------------------------------------------------------

class HubAgentSync(BaseModel):
    """Single agent entry in a sync request."""

    local_agent_id: str
    name: str
    description: str
    capabilities: list[str] = []
    agent_card: dict  # Standard A2A AgentCard as JSON


class HubAgentSyncRequest(BaseModel):
    """POST /relay/hub/{hub_id}/agents/sync body."""

    agents: list[HubAgentSync]


class HubAgentSyncResponse(BaseModel):
    """Response from the agent sync endpoint."""

    synced: list[dict]  # [{agent_id, local_agent_id}, ...]


# ---------------------------------------------------------------------------
# Hub status
# ---------------------------------------------------------------------------

class HubStatus(BaseModel):
    hub_id: str
    is_online: bool
    last_connected_at: datetime | None = None
    agent_count: int = 0


class HubStatusResponse(BaseModel):
    hubs: list[HubStatus]
