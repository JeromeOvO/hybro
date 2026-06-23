"""A2A Webhook API route — thin FastAPI wrapper.

Delegates all business logic to ``WebhookTransport.handle_webhook()``.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.params import Depends as DependsParam

from api_gateway.dependencies import get_webhook_receiver
from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from common.protocols import JsonMap, WebhookReceiver
from common.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _resolve_dependency(value: object, provider) -> object:
    if isinstance(value, DependsParam):
        return provider()
    return value


@router.post("/webhooks/a2a/{message_id}", response_model=None)
async def handle_a2a_webhook(
    request: Request,
    message_id: str,
    authorization: str = Header(default=""),
    x_a2a_notification_token: str = Header(default="", alias="X-A2A-Notification-Token"),
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

    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}") from e
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload: expected JSON object")

    transport = _resolve_dependency(transport, get_webhook_receiver)
    assert isinstance(transport, WebhookReceiver)
    return await transport.handle_webhook(message_id, payload, token)


_mark_declared_owner(router, __name__)
