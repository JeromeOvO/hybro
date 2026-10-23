"""Declarative traffic policies owned by the API Gateway."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoutePolicy:
    auth: str
    tags: tuple[str, ...]
    cors: str = "default"
    api_key: bool = False
    deprecated: bool = False


ROUTE_POLICIES: dict[str, RoutePolicy] = {
    # Remote Agents authenticate with the per-call push token Execution verifies
    # against the call ledger digest, never a Clerk session.
    "a2a_push": RoutePolicy(auth="push-token-route-level", tags=("a2a_push",)),
    "agent": RoutePolicy(auth="mixed-route-level", tags=("agent",)),
    "agent_group": RoutePolicy(auth="clerk-route-level", tags=("agent_group",)),
    "files": RoutePolicy(auth="clerk-route-level", tags=("files",)),
    "hitl": RoutePolicy(auth="clerk-route-level", tags=("hitl",)),
    "inspection": RoutePolicy(auth="clerk-global", tags=("inspection",)),
    # Bundled Agents authenticate with the scoped internal inference token, never
    # a Clerk session or a Provider credential.
    "llm_proxy": RoutePolicy(auth="internal-token-route-level", tags=("internal_llm",)),
    "room": RoutePolicy(auth="clerk-route-level", tags=("room",)),
    "session": RoutePolicy(auth="clerk-route-level", tags=("session",)),
    "sse": RoutePolicy(auth="query-token-supported", tags=("sse",)),
}
