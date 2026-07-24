"""A2A Webhook API route — thin FastAPI wrapper.

Delegates all business logic to ``WebhookTransport.handle_webhook()``.
"""

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from api_gateway.dependencies import get_webhook_receiver
from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from common.protocols import JsonMap, WebhookReceiver
from common.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()
MAX_A2A_WEBHOOK_BODY_BYTES = 139_810_136 + 2 * 1024 * 1024


async def _bounded_json_object(request: Request) -> JsonMap:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_A2A_WEBHOOK_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Payload too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_A2A_WEBHOOK_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")
    try:
        payload = json.loads(body)
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

    payload = await _bounded_json_object(request)
    assert isinstance(transport, WebhookReceiver)
    return await transport.handle_webhook(message_id, payload, token)


_mark_declared_owner(router, __name__)
