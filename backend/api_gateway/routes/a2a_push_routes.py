"""A2A push-notification ingress for canonical Agent calls.

Agents post 1.0 ``StreamResponse`` notifications here with the per-call token
Hybro registered at dispatch. Execution authenticates the token against the
call ledger and owns everything after; this route only bounds the body and maps
rejections to uniform responses.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse

from api_gateway.dependencies import A2APushReceiverDependency
from api_gateway.registry import mark_declared_owner

# Header the A2A SDK push sender uses for TaskPushNotificationConfig.token.
_TOKEN_HEADER = "X-A2A-Notification-Token"

router = APIRouter(
    prefix="/a2a/push",
    responses={
        "default": {
            "description": (
                "Rejected push notification (400 invalid, 401 unauthorized, "
                "413 too large, 503 unavailable)."
            )
        }
    },
)

_REJECTIONS: dict[str, tuple[int, str]] = {
    "invalid": (400, "Invalid push notification"),
    "unauthorized": (401, "Push notification rejected"),
    "too_large": (413, "Payload too large"),
    "unavailable": (503, "Push notification could not be accepted"),
}


def _rejection(outcome: str) -> JSONResponse:
    status_code, detail = _REJECTIONS[outcome]
    return JSONResponse({"detail": detail}, status_code=status_code)


def _declared_length_rejection(request: Request, max_bytes: int) -> str | None:
    """Reject from Content-Length alone; it says nothing about the call."""
    declared = request.headers.get("content-length")
    if declared is None:
        return None
    if not declared.isdigit():
        return "invalid"
    return "too_large" if int(declared) > max_bytes else None


async def _bounded_body(request: Request, max_bytes: int) -> bytes | str:
    """Read at most ``max_bytes``; return the rejection outcome otherwise."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            return "too_large"
    return bytes(body)


@router.post("/{call_record_id}", status_code=202, response_class=Response)
async def receive_a2a_push(
    call_record_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    receiver: A2APushReceiverDependency,
) -> Response:
    rejection = _declared_length_rejection(request, receiver.max_body_bytes)
    if rejection is not None:
        return _rejection(rejection)
    token = request.headers.get(_TOKEN_HEADER)
    # Authenticate from the path and header before reading any body bytes.
    authorized = await receiver.authenticate(call_record_id, token=token)
    if authorized.outcome != "accepted":
        return _rejection(authorized.outcome)
    body = await _bounded_body(request, receiver.max_body_bytes)
    if isinstance(body, str):
        return _rejection(body)
    receipt = await receiver.receive(call_record_id, token=token, body=body)
    if receipt.outcome != "accepted":
        return _rejection(receipt.outcome)
    if receipt.observation_id is not None:
        # Recorded evidence is durable already; inbox recovery retries it.
        background_tasks.add_task(receiver.process, receipt.observation_id)
    return Response(status_code=202)


mark_declared_owner(router, __name__)
