"""Root API Gateway router for all `/api/v1/*` traffic."""

from fastapi import APIRouter, Depends

from api_gateway.routes import (
    a2a_task_routes,
    agent_group_routes,
    agent_routes,
    discovery_api_key_routes,
    discovery_routes,
    files_routes,
    hitl_routes,
    hub_routes,
    inspection_routes,
    memory_routes,
    orchestration_routes,
    platform_gateway_routes,
    relay_routes,
    room_routes,
    sse_routes,
    task_routes,
    webhook_routes,
)
from common.auth import get_current_user


def build_api_gateway_router() -> APIRouter:
    gateway_router = APIRouter()

    gateway_router.include_router(agent_routes.router, tags=["agent"])
    gateway_router.include_router(
        inspection_routes.router,
        tags=["inspection"],
        dependencies=[Depends(get_current_user)],
    )
    gateway_router.include_router(
        memory_routes.router,
        tags=["memory"],
        dependencies=[Depends(get_current_user)],
    )
    gateway_router.include_router(
        orchestration_routes.router,
        tags=["orchestration"],
        dependencies=[Depends(get_current_user)],
    )
    gateway_router.include_router(room_routes.router, tags=["room"])
    gateway_router.include_router(hitl_routes.router, tags=["hitl"])
    gateway_router.include_router(hub_routes.router, tags=["hub"])
    gateway_router.include_router(
        task_routes.router,
        tags=["task"],
        dependencies=[Depends(get_current_user)],
    )
    gateway_router.include_router(sse_routes.router, tags=["sse"])
    gateway_router.include_router(agent_group_routes.router, tags=["agent_group"])
    gateway_router.include_router(files_routes.router, tags=["files"])
    gateway_router.include_router(discovery_routes.router, tags=["discovery"])
    gateway_router.include_router(
        discovery_api_key_routes.router,
        tags=["api_keys"],
    )
    gateway_router.include_router(a2a_task_routes.router, tags=["a2a_tasks"])
    gateway_router.include_router(
        platform_gateway_routes.router,
        tags=["gateway"],
    )
    gateway_router.include_router(relay_routes.router, tags=["relay"])
    gateway_router.include_router(webhook_routes.router, tags=["webhooks"])

    return gateway_router


router = build_api_gateway_router()
