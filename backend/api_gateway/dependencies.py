"""Gateway dependency binding surface.

The first consolidation pass keeps existing route modules as dependency holders
while API Gateway owns HTTP route registration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class APIGatewayDeps:
    file_storage: Any
    relay_service: Any
    execution_deps: Any
    platform_facade: Any


_deps: APIGatewayDeps | None = None


def missing_gateway_route_bindings() -> list[str]:
    from api_gateway.routes import (
        a2a_task_routes,
        agent_group_routes,
        agent_routes,

        hitl_routes,
        hub_routes,
        inspection_routes,
        memory_routes,
        relay_routes,
        room_routes,
        sse_routes,
        webhook_routes,
    )
    from api_gateway.viewsets import agent as agent_viewset
    from api_gateway.viewsets import base as viewset

    bindings = {
        "api_gateway.routes.a2a_task_routes": (a2a_task_routes, ("task_store",)),
        "api_gateway.routes.agent_group_routes": (agent_group_routes, ("agent_group_store",)),
        "api_gateway.routes.agent_routes": (
            agent_routes,
            (
                "agent_center",
                "agent_service",
                "capability_issue_service",
                "agent_avatar_manager",
                "agent_liveness_checker",
            ),
        ),

        "api_gateway.routes.hitl_routes": (
            hitl_routes,
            ("hitl_manager", "room_ownership_reader"),
        ),
        "api_gateway.routes.hub_routes": (hub_routes, ("hub_relay_service",)),
        "api_gateway.routes.inspection_routes": (
            inspection_routes,
            ("inspection_center",),
        ),
        "api_gateway.routes.memory_routes": (memory_routes, ("memory_center",)),
        "api_gateway.routes.relay_routes": (relay_routes, ("relay_service",)),
        "api_gateway.routes.room_routes": (
            room_routes,
            ("room_center", "room_store", "agent_selection_service", "execution_engine"),
        ),
        "api_gateway.routes.sse_routes": (
            sse_routes,
            ("execution_engine", "sse_store", "sse_manager"),
        ),
        "api_gateway.routes.webhook_routes": (
            webhook_routes,
            ("webhook_receiver",),
        ),
        "api_gateway.viewsets.agent": (
            agent_viewset,
            ("embedding_provider", "vector_index"),
        ),
        "api_gateway.viewsets.base": (viewset, ("repository_provider",)),
    }

    missing = []
    for module_name, (module, names) in bindings.items():
        for name in names:
            if getattr(module, name) is None:
                missing.append(f"{module_name}.{name}")
    return missing


def missing_required_deps(deps: APIGatewayDeps | None = None) -> list[str]:
    include_route_bindings = deps is None
    if deps is None:
        deps = _deps
    if deps is None:
        return ["api_gateway.dependencies"]

    missing = []
    for field_name in APIGatewayDeps.__dataclass_fields__:
        if field_name in {"file_storage", "platform_facade"}:
            continue
        if getattr(deps, field_name) is None:
            missing.append(field_name)
    if include_route_bindings:
        missing.extend(missing_gateway_route_bindings())
    return missing


def bind_api_gateway_deps(deps: APIGatewayDeps) -> None:
    missing = missing_required_deps(deps)
    if missing:
        raise RuntimeError(
            "APIGatewayDeps incomplete - missing: " + ", ".join(missing)
        )

    global _deps
    _deps = deps


def get_api_gateway_deps() -> APIGatewayDeps:
    if _deps is None:
        raise RuntimeError("APIGatewayDeps not bound - startup incomplete")
    return _deps


def is_bound() -> bool:
    return _deps is not None and not missing_required_deps()
