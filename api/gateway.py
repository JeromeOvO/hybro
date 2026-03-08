"""
Gateway API Router for Hybro Hub Phase 1

Exposes authenticated endpoints that let external SDK/hub consumers discover
and communicate with cloud agents via the Hybro Gateway.

All endpoints require X-API-Key authentication.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from common.api_key_auth import get_api_key
from common.utils.logger import get_logger
from models.api_key import APIKey
from models.gateway import (
    GatewayCardResponse,
    GatewayDiscoverRequest,
    GatewayDiscoveryResponse,
    GatewaySendRequest,
)
from services.gateway_rate_limit_service import gateway_rate_limit_service
from services.gateway_service import (
    GatewayService,
    gateway_service,
)

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_svc() -> GatewayService:
    return gateway_service


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
    svc: GatewayService = Depends(_get_svc),
):
    await gateway_rate_limit_service.check_rate_limit(api_key)
    try:
        result = await svc.discover_agents(
            query=body.query,
            limit=body.limit,
            user_id=api_key.user_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Gateway discover failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "discovery_error", "message": "Agent discovery service unavailable"},
        ) from e
    await gateway_rate_limit_service.record_request(api_key)
    return result


@router.post(
    "/gateway/agents/{agent_id}/message/send",
    summary="Send a synchronous message to an agent",
)
async def gateway_send(
    agent_id: str,
    body: GatewaySendRequest,
    api_key: APIKey = Depends(get_api_key),
    svc: GatewayService = Depends(_get_svc),
):
    await gateway_rate_limit_service.check_rate_limit(api_key)
    result = await svc.send_message(
        agent_id=agent_id,
        message=body.message,
        user_id=api_key.user_id,
    )
    await gateway_rate_limit_service.record_request(api_key)
    return result


@router.post(
    "/gateway/agents/{agent_id}/message/stream",
    summary="Stream a message to an agent (SSE)",
)
async def gateway_stream(
    agent_id: str,
    body: GatewaySendRequest,
    api_key: APIKey = Depends(get_api_key),
    svc: GatewayService = Depends(_get_svc),
):
    await gateway_rate_limit_service.check_rate_limit(api_key)

    event_stream = await svc.prepare_stream(
        agent_id=agent_id,
        message=body.message,
        user_id=api_key.user_id,
    )

    async def _event_generator():
        try:
            async for event in event_stream:
                yield f"data: {event.model_dump_json()}\n\n"
        except Exception as e:
            logger.error(f"Gateway SSE stream error for agent {agent_id}: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            await gateway_rate_limit_service.record_request(api_key)

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
    svc: GatewayService = Depends(_get_svc),
):
    await gateway_rate_limit_service.check_rate_limit(api_key)
    card = await svc.get_agent_card(agent_id=agent_id, user_id=api_key.user_id)
    await gateway_rate_limit_service.record_request(api_key)
    return GatewayCardResponse(agent_id=agent_id, agent_card=card)
