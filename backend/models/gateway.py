from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class GatewayDiscoverRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    query: str
    limit: int | None = Field(default=None, ge=1, le=100)


class GatewaySendRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message: Any


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class GatewayCardResponse(BaseModel):
    agent_id: str
    agent_card: dict


class GatewayDiscoveryAgentResult(BaseModel):
    """Discovery result enriched with agent_id for gateway consumers."""

    agent_id: str
    agent_card: dict
    match_score: float


class GatewayDiscoveryResponse(BaseModel):
    """Gateway discovery response with agent_id on each result."""

    query: str
    agents: list[GatewayDiscoveryAgentResult]
    count: int
