"""Hub status API — Clerk-authenticated endpoints for the web frontend.

Provides hub metadata to the portal UI. Separate from the relay API which
uses API key auth for hub daemon communication.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.params import Depends as DependsParam

from common.auth import ClerkUser, get_current_user
from models.hub import HubStatusResponse

router = APIRouter(prefix="/hub")
hub_relay_service = None


def bind_hub_dependencies(service) -> None:
    global hub_relay_service
    hub_relay_service = service


def get_hub_relay_service():
    if hub_relay_service is None:
        raise RuntimeError("Hub relay dependency has not been bound")
    return hub_relay_service


def _resolve_dependency(value, provider):
    if isinstance(value, DependsParam):
        return provider()
    return value


@router.get("/my-status", response_model=HubStatusResponse)
async def hub_status_for_user(
    user: ClerkUser = Depends(get_current_user),
    svc=Depends(get_hub_relay_service),
):
    svc = _resolve_dependency(svc, get_hub_relay_service)
    hubs = await svc.get_hub_status(user.user_id)
    return HubStatusResponse(hubs=hubs)
