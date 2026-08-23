"""A2A Webhook API route — thin FastAPI wrapper.

Delegates all business logic to ``WebhookTransport.handle_webhook()``.
"""

import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from api_gateway.dependencies import get_webhook_receiver
from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from common.protocols import JsonMap, WebhookReceiver
from execution.orchestrator_routing import (
    OWNER_LEGACY,
    OWNER_ORCHESTRATOR,
    WebhookAuthenticationError,
)

router = APIRouter()
MAX_A2A_WEBHOOK_BODY_BYTES = 139_810_136 + 2 * 1024 * 1024


async def _bounded_json_object(request: Request) -> JsonMap:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_A2A_WEBHOOK_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Payload too large")
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid Content-Length"
            ) from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_A2A_WEBHOOK_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")
    try:
        payload = await asyncio.to_thread(json.loads, body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400, detail="Invalid payload: expected JSON object"
        )
    return payload


@router.post("/webhooks/a2a/{message_id}", response_model=None)
async def handle_a2a_webhook(
    request: Request,
    message_id: str,
    authorization: str = Header(default=""),
    x_a2a_notification_token: str = Header(
        default="", alias="X-A2A-Notification-Token"
    ),
    transport: WebhookReceiver = Depends(get_webhook_receiver),
) -> JsonMap:
    """Receive task updates from A2A agents.

    Per A2A spec section 4.3.3, payload is StreamResponse format.
    Security: Validates token against stored hash.
    Idempotency: Safe to call multiple times with same status.
    """
    token = x_a2a_notification_token or (
        authorization.replace("Bearer ", "") if authorization else ""
    )

    assert isinstance(transport, WebhookReceiver)
    payload = await _bounded_json_object(request)
    # The parsed payload is reused below by both the routing seam and the
    # legacy transport, so the body is parsed exactly once.
    routing_state = getattr(getattr(request, "app", None), "state", None)
    router = getattr(routing_state, "orchestrator_routing", None)
    owner = OWNER_LEGACY
    if router is not None:
        try:
            owner = await router.route_webhook(
                message_id=message_id, payload=payload, token=token
            )
        except WebhookAuthenticationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except HTTPException:
            raise
    if owner == OWNER_ORCHESTRATOR:
        return {"status": "accepted"}
    await transport.authenticate_webhook(message_id, token)
    return await transport.handle_webhook(message_id, payload, token)


_mark_declared_owner(router, __name__)
