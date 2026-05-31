"""Relay API Router — hub registration, SSE events, publish, agent sync, status.

All endpoints require X-API-Key authentication (reuses common.api_key_auth).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.params import Depends as DependsParam
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from common.api_key_auth import get_api_key, get_api_key_no_track
from common.protocols import HubRelayManagement
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

relay_service: HubRelayManagement | None = None


def bind_relay_dependencies(service: HubRelayManagement) -> None:
    global relay_service

    relay_service = service


def get_relay_service() -> HubRelayManagement:
    if relay_service is None:
        raise RuntimeError("Relay service dependency has not been bound")
    return relay_service


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


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
    api_key: APIKey = Depends(get_api_key_no_track),
    svc: HubRelayManagement = Depends(get_relay_service),
):
    svc = _resolve_dependency(svc, get_relay_service)
    hub = await svc.register_hub(body.hub_id, api_key)
    return RegisterHubResponse(hub_id=hub.hub_id, user_id=hub.user_id)


# ------------------------------------------------------------------
# GET /relay/hub/{hub_id}/events  (SSE)
# ------------------------------------------------------------------

@router.get("/hub/{hub_id}/events")
async def relay_events(
    hub_id: str,
    request: Request,
    last_event_id: str | None = Query(None),
    api_key: APIKey = Depends(get_api_key_no_track),
    svc: HubRelayManagement = Depends(get_relay_service),
):
    svc = _resolve_dependency(svc, get_relay_service)

    # Prefer standard SSE header, fall back to query param
    resume_from = request.headers.get("Last-Event-ID") or last_event_id

    async def event_generator():
        try:
            async for event in svc.connect_hub(hub_id, api_key, last_event_id=resume_from):
                if await request.is_disconnected():
                    break
                if isinstance(event, dict) and event.get("type") == "_disconnect":
                    break
                stream_id = event.pop("_stream_id", None) if isinstance(event, dict) else None
                data = json.dumps(event)
                if stream_id:
                    yield f"id: {stream_id}\ndata: {data}\n\n"
                else:
                    yield f"data: {data}\n\n"
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
    api_key: APIKey = Depends(get_api_key),
    svc: HubRelayManagement = Depends(get_relay_service),
):
    svc = _resolve_dependency(svc, get_relay_service)

    try:
        await svc.process_publish(hub_id, body, api_key)
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
    api_key: APIKey = Depends(get_api_key_no_track),
    svc: HubRelayManagement = Depends(get_relay_service),
):
    svc = _resolve_dependency(svc, get_relay_service)
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
    svc: HubRelayManagement = Depends(get_relay_service),
):
    svc = _resolve_dependency(svc, get_relay_service)
    hubs = await svc.get_hub_status(api_key.user_id)
    return HubStatusResponse(hubs=hubs)


# ------------------------------------------------------------------
# POST /relay/hub/{hub_id}/heartbeat
# ------------------------------------------------------------------

@router.post("/hub/{hub_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def relay_heartbeat(
    hub_id: str,
    api_key: APIKey = Depends(get_api_key_no_track),
    svc: HubRelayManagement = Depends(get_relay_service),
):
    """Lightweight liveness signal from the hub daemon."""
    svc = _resolve_dependency(svc, get_relay_service)
    try:
        await svc.record_hub_heartbeat(hub_id, api_key)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc


_mark_declared_owner(router, __name__)
