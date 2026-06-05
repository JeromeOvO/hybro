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
    "a2a_task": RoutePolicy(auth="clerk-route-level", tags=("a2a_tasks",)),
    "agent": RoutePolicy(auth="mixed-route-level", tags=("agent",)),
    "agent_group": RoutePolicy(auth="clerk-route-level", tags=("agent_group",)),
    "discovery": RoutePolicy(
        auth="api-key-route-level",
        tags=("discovery",),
        cors="open",
        api_key=True,
    ),
    "discovery_api_key": RoutePolicy(auth="clerk-route-level", tags=("api_keys",)),
    "files": RoutePolicy(auth="clerk-route-level", tags=("files",)),
    "hitl": RoutePolicy(auth="clerk-route-level", tags=("hitl",)),
    "hub": RoutePolicy(auth="clerk-route-level", tags=("hub",)),
    "inspection": RoutePolicy(auth="clerk-global", tags=("inspection",)),
    "memory": RoutePolicy(auth="clerk-global", tags=("memory",)),
    "platform_gateway": RoutePolicy(
        auth="api-key-route-level",
        tags=("gateway",),
        cors="open",
        api_key=True,
    ),
    "relay": RoutePolicy(
        auth="api-key-route-level",
        tags=("relay",),
        cors="open",
        api_key=True,
    ),
    "room": RoutePolicy(auth="clerk-route-level", tags=("room",)),
    "sse": RoutePolicy(auth="query-token-supported", tags=("sse",)),
    "webhook": RoutePolicy(auth="bearer-token-route-level", tags=("webhooks",)),
}


def open_cors_groups() -> frozenset[str]:
    return frozenset(
        group for group, policy in ROUTE_POLICIES.items() if policy.cors == "open"
    )
