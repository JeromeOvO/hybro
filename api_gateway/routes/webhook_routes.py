"""A2A Webhook API route — thin FastAPI wrapper.

Delegates all business logic to ``WebhookTransport.handle_webhook()``.
"""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.params import Depends as DependsParam

from app_shell.bound import WebhookTransport, WebhookTransportFactory
from common.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()

webhook_transport_factory: WebhookTransportFactory | None = None


def bind_webhook_dependencies(factory: WebhookTransportFactory) -> None:
    global webhook_transport_factory

    webhook_transport_factory = factory


def get_webhook_transport() -> WebhookTransport:
    if webhook_transport_factory is None:
        raise RuntimeError("Webhook transport dependency has not been bound")
    return webhook_transport_factory()


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


@router.post("/webhooks/a2a/{message_id}")
async def handle_a2a_webhook(
    request: Request,
    message_id: str,
    authorization: str = Header(default=""),
    x_a2a_notification_token: str = Header(default="", alias="X-A2A-Notification-Token"),
    transport: WebhookTransport = Depends(get_webhook_transport),
) -> dict[str, Any]:
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

    transport = _resolve_dependency(transport, get_webhook_transport)
    return await transport.handle_webhook(message_id, payload, token)


from api_gateway.registry import mark_declared_owner as _mark_declared_owner

_mark_declared_owner(router, __name__)
