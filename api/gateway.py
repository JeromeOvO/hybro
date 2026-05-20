"""
Gateway API Router for Hybro Hub Phase 1

Exposes authenticated endpoints that let external SDK/hub consumers discover
and communicate with cloud agents via the Hybro Gateway.

All endpoints require X-API-Key authentication.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.params import Depends as DependsParam
from fastapi.responses import StreamingResponse

from common.api_key_auth import get_api_key
from common.errors import GatewayPlatformError, PlatformRouteError
from common.utils.logger import get_logger
from models.api_key import APIKey
from models.gateway import (
    GatewayCardResponse,
    GatewayDiscoverRequest,
    GatewayDiscoveryResponse,
    GatewaySendRequest,
)

logger = get_logger(__name__)

router = APIRouter()

gateway_service: Any | None = None
gateway_rate_limit_service: Any | None = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def bind_gateway_dependencies(service: Any, rate_limiter: Any) -> None:
    global gateway_service, gateway_rate_limit_service

    gateway_service = service
    gateway_rate_limit_service = rate_limiter


def get_gateway_service() -> Any:
    if gateway_service is None:
        raise RuntimeError("Gateway service dependency has not been bound")
    return gateway_service


def get_gateway_rate_limiter() -> Any:
    if gateway_rate_limit_service is None:
        raise RuntimeError("Gateway rate limiter dependency has not been bound")
    return gateway_rate_limit_service


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


async def _check_rate_limit(rate_limiter: Any, api_key: APIKey) -> None:
    try:
        await rate_limiter.check_rate_limit(api_key)
    except PlatformRouteError as exc:
        _raise_http_error(exc)


async def _record_request(rate_limiter: Any, api_key: APIKey) -> None:
    await rate_limiter.record_request(api_key)


def _raise_http_error(error: PlatformRouteError) -> None:
    headers = None
    detail = error.detail
    if "retry_after" in error.detail:
        headers = {"Retry-After": str(error.detail["retry_after"])}
        detail = dict(error.detail)
        detail.pop("retry_after", None)
    raise HTTPException(
        status_code=error.status_code,
        detail=detail,
        headers=headers,
    ) from error


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/gateway/agents/discover",
    response_model=GatewayDiscoveryResponse,
    summary="Discover cloud agents",
)
async def gateway_discover(
    body: GatewayDiscoverRequest,
    api_key: APIKey = Depends(get_api_key),
    svc: Any = Depends(get_gateway_service),
    rate_limiter: Any = Depends(get_gateway_rate_limiter),
):
    rate_limiter = _resolve_dependency(rate_limiter, get_gateway_rate_limiter)
    await _check_rate_limit(rate_limiter, api_key)
    try:
        result = await svc.discover_agents(
            query=body.query,
            limit=body.limit,
            user_id=api_key.user_id,
        )
    except HTTPException:
        raise
    except GatewayPlatformError as exc:
        _raise_http_error(exc)
    except Exception as e:
        logger.error(f"Gateway discover failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "discovery_error", "message": "Agent discovery service unavailable"},
        ) from e
    await _record_request(rate_limiter, api_key)
    return result


@router.post(
    "/gateway/agents/{agent_id}/message/send",
    summary="Send a synchronous message to an agent",
)
async def gateway_send(
    agent_id: str,
    body: GatewaySendRequest,
    api_key: APIKey = Depends(get_api_key),
    svc: Any = Depends(get_gateway_service),
    rate_limiter: Any = Depends(get_gateway_rate_limiter),
):
    rate_limiter = _resolve_dependency(rate_limiter, get_gateway_rate_limiter)
    await _check_rate_limit(rate_limiter, api_key)
    try:
        result = await svc.send_message(
            agent_id=agent_id,
            message=body.message,
            user_id=api_key.user_id,
        )
    except GatewayPlatformError as exc:
        _raise_http_error(exc)
    await _record_request(rate_limiter, api_key)
    return result


@router.post(
    "/gateway/agents/{agent_id}/message/stream",
    summary="Stream a message to an agent (SSE)",
)
async def gateway_stream(
    agent_id: str,
    body: GatewaySendRequest,
    api_key: APIKey = Depends(get_api_key),
    svc: Any = Depends(get_gateway_service),
    rate_limiter: Any = Depends(get_gateway_rate_limiter),
):
    rate_limiter = _resolve_dependency(rate_limiter, get_gateway_rate_limiter)
    await _check_rate_limit(rate_limiter, api_key)

    try:
        event_stream = await svc.prepare_stream(
            agent_id=agent_id,
            message=body.message,
            user_id=api_key.user_id,
        )
    except GatewayPlatformError as exc:
        _raise_http_error(exc)

    async def _event_generator():
        try:
            async for event in event_stream:
                if hasattr(event, "model_dump_json"):
                    payload = event.model_dump_json()
                else:
                    payload = json.dumps(event)
                yield f"data: {payload}\n\n"
        except Exception as e:
            logger.error(f"Gateway SSE stream error for agent {agent_id}: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            await _record_request(rate_limiter, api_key)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/gateway/agents/{agent_id}/card",
    response_model=GatewayCardResponse,
    summary="Get an agent's card with masked URL",
)
async def gateway_get_card(
    agent_id: str,
    api_key: APIKey = Depends(get_api_key),
    svc: Any = Depends(get_gateway_service),
    rate_limiter: Any = Depends(get_gateway_rate_limiter),
):
    rate_limiter = _resolve_dependency(rate_limiter, get_gateway_rate_limiter)
    await _check_rate_limit(rate_limiter, api_key)
    try:
        card = await svc.get_agent_card(agent_id=agent_id, user_id=api_key.user_id)
    except GatewayPlatformError as exc:
        _raise_http_error(exc)
    await _record_request(rate_limiter, api_key)
    return GatewayCardResponse(agent_id=agent_id, agent_card=card)
