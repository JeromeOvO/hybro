"""Hub status API — Clerk-authenticated endpoints for the web frontend.

Provides hub metadata to the portal UI. Separate from the relay API which
uses API key auth for hub daemon communication.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from common.auth import ClerkUser, get_current_user
from models.hub import HubStatusResponse
from api.relay import get_relay_service, _resolve_dependency

router = APIRouter(prefix="/hub")


@router.get("/my-status", response_model=HubStatusResponse)
async def hub_status_for_user(
    user: ClerkUser = Depends(get_current_user),
    svc: Any = Depends(get_relay_service),
):
    """Return hub connection status for the authenticated user."""
    svc = _resolve_dependency(svc, get_relay_service)
    hubs = await svc.get_hub_status(user.user_id)
    return HubStatusResponse(hubs=hubs)
