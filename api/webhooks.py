"""A2A Webhook API route — thin FastAPI wrapper.

Delegates all business logic to ``WebhookTransport.handle_webhook()``.
"""

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from common.utils.logger import get_logger
from modules.agent_response_handler import AgentResponseHandler
from modules.transports.webhook import WebhookTransport, parse_stream_response  # noqa: F401
from services.database_service import db_service
from services.sse_services import sse_manager

logger = get_logger(__name__)

router = APIRouter()


def _get_webhook_transport() -> WebhookTransport:
    from modules.RoomMessageCenter import room_message_center

    handler = AgentResponseHandler(
        db=db_service,
        sse=sse_manager,
        room_message_center=room_message_center,
    )
    return WebhookTransport(response_handler=handler, db=db_service)


@router.post("/webhooks/a2a/{message_id}")
async def handle_a2a_webhook(
    request: Request,
    message_id: str,
    authorization: str = Header(default=""),
    x_a2a_notification_token: str = Header(default="", alias="X-A2A-Notification-Token"),
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

    transport = _get_webhook_transport()
    return await transport.handle_webhook(message_id, payload, token)
