"""Relay API Router — hub registration, SSE events, publish, agent sync, status.

All endpoints require X-API-Key authentication (reuses common.api_key_auth)
except /publish which uses a connection-scoped JWT.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from common.api_key_auth import get_api_key
from common.utils.logger import get_logger
from models.api_key import APIKey
from models.hub import (
    HubAgentSyncRequest,
    HubAgentSyncResponse,
    HubPublishRequest,
    HubStatusResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/relay")


def _get_relay_service():
    from services.relay_service import relay_service
    return relay_service


# ------------------------------------------------------------------
# POST /relay/hub/register
# ------------------------------------------------------------------


class RegisterHubRequest(BaseModel):
    hub_id: str


class RegisterHubResponse(BaseModel):
    hub_id: str
    user_id: str


@router.post("/hub/register", response_model=RegisterHubResponse)
async def relay_register(
    body: RegisterHubRequest,
    api_key: APIKey = Depends(get_api_key),
):
    svc = _get_relay_service()
    hub = await svc.register_hub(body.hub_id, api_key)
    return RegisterHubResponse(hub_id=hub.hub_id, user_id=hub.user_id)


# ------------------------------------------------------------------
# GET /relay/hub/{hub_id}/events  (SSE)
# ------------------------------------------------------------------

@router.get("/hub/{hub_id}/events")
async def relay_events(
    hub_id: str,
    request: Request,
    api_key: APIKey = Depends(get_api_key),
):
    svc = _get_relay_service()

    async def event_generator():
        try:
            async for event in svc.connect_hub(hub_id, api_key):
                if await request.is_disconnected():
                    break
                if isinstance(event, dict) and event.get("type") == "_disconnect":
                    break
                yield f"data: {json.dumps(event)}\n\n"
        except PermissionError as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------
# POST /relay/hub/{hub_id}/publish
# ------------------------------------------------------------------

@router.post("/hub/{hub_id}/publish", status_code=status.HTTP_204_NO_CONTENT)
async def relay_publish(
    hub_id: str,
    body: HubPublishRequest,
    authorization: str = Header(..., alias="Authorization"),
):
    svc = _get_relay_service()

    # Extract bearer token
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be Bearer <token>",
        )
    token = authorization[len("Bearer "):]

    try:
        await svc.process_publish(hub_id, body, token)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


# ------------------------------------------------------------------
# POST /relay/hub/{hub_id}/agents/sync
# ------------------------------------------------------------------

@router.post(
    "/hub/{hub_id}/agents/sync", response_model=HubAgentSyncResponse
)
async def relay_sync_agents(
    hub_id: str,
    body: HubAgentSyncRequest,
    api_key: APIKey = Depends(get_api_key),
):
    svc = _get_relay_service()
    try:
        synced = await svc.sync_agents(
            hub_id, body.agents, api_key, prune_missing=body.prune_missing,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    return HubAgentSyncResponse(synced=synced)


# ------------------------------------------------------------------
# GET /relay/hub/status
# ------------------------------------------------------------------

@router.get("/hub/status", response_model=HubStatusResponse)
async def relay_status(
    api_key: APIKey = Depends(get_api_key),
):
    svc = _get_relay_service()
    hubs = await svc.get_hub_status(api_key.user_id)
    return HubStatusResponse(hubs=hubs)


# ------------------------------------------------------------------
# POST /relay/hub/{hub_id}/heartbeat
# ------------------------------------------------------------------

@router.post("/hub/{hub_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def relay_heartbeat(
    hub_id: str,
    api_key: APIKey = Depends(get_api_key),
):
    """Lightweight liveness signal from the hub daemon."""
    svc = _get_relay_service()
    try:
        svc.record_hub_heartbeat(hub_id, api_key)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
