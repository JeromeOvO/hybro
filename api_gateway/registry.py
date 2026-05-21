"""Route inventory and ownership helpers for the API Gateway."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter

DECLARED_OWNER_ATTR = "__api_gateway_declared_owner__"


def mark_declared_owner(router: APIRouter, owner: str) -> None:
    """Attach gateway ownership metadata to every endpoint in a router."""
    for route in router.routes:
        setattr(route, DECLARED_OWNER_ATTR, owner)
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            setattr(endpoint, DECLARED_OWNER_ATTR, owner)


def include_owned_router(target: APIRouter, source: APIRouter, *, owner: str) -> None:
    mark_declared_owner(source, owner)
    target.include_router(source)
    mark_declared_owner(target, owner)


def resolve_declared_owner(route: Any) -> str:
    route_owner = getattr(route, DECLARED_OWNER_ATTR, None)
    if isinstance(route_owner, str) and route_owner:
        return route_owner

    endpoint = getattr(route, "endpoint", None)
    endpoint_owner = getattr(endpoint, DECLARED_OWNER_ATTR, None)
    if isinstance(endpoint_owner, str) and endpoint_owner:
        return endpoint_owner

    return getattr(endpoint, "__module__", "")


def route_group_for_path(path: str) -> str:
    normalized = path.removeprefix("/api/v1")

    if normalized.startswith("/a2a-tasks/") or "/a2a-tasks" in normalized:
        return "a2a_task"
    if normalized.startswith("/agents"):
        return "agent"
    if normalized.startswith("/api-keys"):
        return "discovery_api_key"
    if normalized.startswith("/agentGroups"):
        return "agent_group"
    if normalized.startswith("/agent/"):
        return "agent"
    if normalized.startswith("/discovery/api-keys"):
        return "discovery_api_key"
    if normalized.startswith("/discovery"):
        return "discovery"
    if normalized.startswith("/files/"):
        return "files"
    if normalized.startswith("/gateway/"):
        return "platform_gateway"
    if normalized.startswith("/hub/"):
        return "hub"
    if normalized.startswith("/inspectionCenter/"):
        return "inspection"
    if normalized.startswith("/memoryCenter/"):
        return "memory"
    if normalized.startswith("/orchestrationCenter/"):
        return "orchestration"
    if normalized.startswith("/relay/"):
        return "relay"
    if normalized.startswith("/roomCenter/"):
        return "room"
    if normalized.startswith("/rooms/") and "/hitl" in normalized:
        return "hitl"
    if normalized.startswith("/sse/"):
        return "sse"
    if normalized.startswith("/task/") or normalized.startswith("/tasks/"):
        return "task"
    if normalized.startswith("/users/me/a2a-tasks"):
        return "a2a_task"
    if normalized.startswith("/webhooks/"):
        return "webhook"
    return "unknown"


def expected_owner_for_group(group: str) -> str:
    return f"api_gateway.routes.{group}_routes"


def open_cors_path_prefixes(api_prefix: str) -> tuple[str, ...]:
    from api_gateway.policies import open_cors_groups

    suffixes = {
        "discovery": "/discovery",
        "platform_gateway": "/gateway",
        "relay": "/relay",
    }
    return tuple(
        f"{api_prefix}{suffixes[group]}"
        for group in sorted(open_cors_groups())
        if group in suffixes
    )


def route_groups_for_paths(paths: Iterable[str]) -> set[str]:
    return {route_group_for_path(path) for path in paths}
