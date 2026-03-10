"""Hub and Relay protocol models.

Defines the Hub registration model and the Pydantic schemas that form
the relay protocol contract between the cloud backend and hub daemons.

Event type vocabularies
-----------------------

**Hub → Cloud** (``HubPublishEvent.type``):
  task_submitted   — agent acknowledged the task
  agent_token      — streaming text token
  agent_response   — final successful response (text + optional parts)
  agent_error      — dispatch or agent-level failure
  processing_status — terminal processing signal (completed / failed)
  artifact_update  — A2A artifact streaming chunk
  task_status      — A2A task status transition
  task_interactive — agent requires user input (HITL)

**Cloud → Hub** (``RelayToHubEvent.type``):
  user_message — new message to dispatch to a local agent
  heartbeat    — keepalive ping
  cancel_task  — request cancellation of an in-flight task
  user_reply   — HITL reply to an interactive task
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

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
    connection_id: str | None = None


# ---------------------------------------------------------------------------
# Relay-to-hub events (cloud -> hub, delivered via SSE)
# ---------------------------------------------------------------------------

RelayToHubEventType = Literal[
    "user_message",
    "heartbeat",
    "cancel_task",
    "user_reply",
]


class RelayToHubEvent(BaseModel):
    """Event pushed from cloud to hub via the SSE stream."""

    type: RelayToHubEventType
    room_id: str | None = None
    user_message_id: str | None = None
    agent_message_id: str | None = None
    agent_id: str | None = None
    local_agent_id: str | None = None
    message: dict | None = None  # A2A Message payload

    # cancel_task / user_reply fields
    task_id: str | None = None
    context_id: str | None = None
    reply_text: str | None = None


# ---------------------------------------------------------------------------
# Hub-to-relay events (hub -> cloud, via POST /publish)
# ---------------------------------------------------------------------------

HubPublishEventType = Literal[
    "task_submitted",
    "agent_token",
    "agent_response",
    "agent_error",
    "processing_status",
    "artifact_update",
    "task_status",
    "task_interactive",
]


class HubPublishEvent(BaseModel):
    """Single event in a publish batch from hub to cloud."""

    type: HubPublishEventType
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
    prune_missing: bool = True


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
