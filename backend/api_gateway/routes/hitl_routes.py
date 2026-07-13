"""HITL (Human-in-the-Loop) REST API endpoints.

Provides endpoints for users to respond to, query, and cancel HITL requests
that are generated when agents or the supervisor need human input.

See docs/HITL_DESIGN.md §7.4 for design details.
"""

from fastapi import APIRouter, Depends, HTTPException

from api_gateway.dependencies import get_hitl_manager, get_room_ownership_reader
from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from common.auth import ClerkUser, get_current_user
from common.dto import HITLRequest
from common.protocols import HITLManager, RoomOwnershipReader
from models.hitl import HITLResponseRequest

router = APIRouter(prefix="/rooms/{room_id}/hitl", tags=["hitl"])


async def verify_room_ownership(
    room_id: str,
    user: ClerkUser,
    room_ownership: RoomOwnershipReader,
) -> None:
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")

    owner_id = await room_ownership.get_room_owner(room_id)
    if owner_id is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if owner_id != user.user_id:
        raise HTTPException(
            status_code=403, detail="You do not have permission to access this room"
        )


_HITL_ERROR_STATUS = {
    "HITLNotFoundError": 404,
    "HITLConflictError": 409,
    "HITLRoomMismatchError": 403,
    "HITLContinuationLostError": 410,
    "HITLRoutingFailedError": 502,
}

_PUBLIC_PENDING_HITL_FIELDS = {
    "request_id",
    "message_id",
    "prompt",
    "prompt_type",
    "choices",
    "source",
    "agent_id",
    "agent_name",
    "source_step_id",
    "created_at",
    "expires_at",
    "group_id",
    "group_total",
    "group_index",
    "client_request_id",
}


def _raise_http_for_hitl_error(exc: Exception) -> None:
    status_code = _HITL_ERROR_STATUS.get(exc.__class__.__name__, 500)
    message = getattr(exc, "message", str(exc))
    raise HTTPException(status_code=status_code, detail=message) from exc


def _pending_hitl_public_payload(request: HITLRequest) -> dict:
    payload = request.model_dump(
        mode="json",
        include=_PUBLIC_PENDING_HITL_FIELDS,
        exclude_none=True,
    )
    payload["message_id"] = (
        request.display_message_id
        or request.continuation_message_id
        or request.message_id
        or request.user_message_id
    )
    payload["related_message_id"] = request.user_message_id
    return payload


@router.post("/respond")
async def respond_to_hitl_request(
    room_id: str,
    body: HITLResponseRequest,
    user: ClerkUser = Depends(get_current_user),
    manager: HITLManager = Depends(get_hitl_manager),
    room_ownership: RoomOwnershipReader = Depends(get_room_ownership_reader),
):
    """User responds to an HITL prompt."""
    await verify_room_ownership(room_id, user, room_ownership)

    try:
        response = await manager.resolve_hitl(
            room_id,
            body.request_id,
            body.user_input,
            user.user_id,
        )
    except Exception as exc:
        _raise_http_for_hitl_error(exc)
    result = {"status": response.status, "request_id": response.request_id}
    if response.reclaimed is not None:
        result["reclaimed"] = response.reclaimed
    return result


@router.get("/pending")
async def get_pending_hitl_requests(
    room_id: str,
    user: ClerkUser = Depends(get_current_user),
    manager: HITLManager = Depends(get_hitl_manager),
    room_ownership: RoomOwnershipReader = Depends(get_room_ownership_reader),
):
    """Get pending HITL requests for a room (SSE reconnect catch-up)."""
    await verify_room_ownership(room_id, user, room_ownership)

    requests = await manager.get_pending_hitl(room_id)
    return {"requests": [_pending_hitl_public_payload(r) for r in requests]}


@router.post("/{request_id}/cancel")
async def cancel_hitl_request(
    room_id: str,
    request_id: str,
    user: ClerkUser = Depends(get_current_user),
    manager: HITLManager = Depends(get_hitl_manager),
    room_ownership: RoomOwnershipReader = Depends(get_room_ownership_reader),
):
    """Cancel a pending HITL request."""
    await verify_room_ownership(room_id, user, room_ownership)

    try:
        await manager.cancel_hitl(room_id, request_id)
    except Exception as exc:
        _raise_http_for_hitl_error(exc)
    return {"status": "canceled"}


_mark_declared_owner(router, __name__)
