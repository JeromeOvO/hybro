"""HITL (Human-in-the-Loop) REST API endpoints.

Provides endpoints for users to respond to, query, and cancel HITL requests
that are generated when agents or the supervisor need human input.

See docs/HITL_DESIGN.md §7.4 for design details.
"""

from fastapi import APIRouter, Depends, HTTPException

from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from common.auth import ClerkUser, get_current_user
from common.protocols import HITLManager, RoomOwnershipReader
from models.hitl import HITLResponseRequest

router = APIRouter(prefix="/rooms/{room_id}/hitl", tags=["hitl"])
hitl_manager: HITLManager | None = None
room_ownership_reader: RoomOwnershipReader | None = None


def bind_execution_deps(deps) -> None:
    global hitl_manager
    hitl_manager = deps.hitl_manager


def bind_room_ownership_reader(reader: RoomOwnershipReader) -> None:
    global room_ownership_reader
    room_ownership_reader = reader


def _require_hitl_manager() -> HITLManager:
    if hitl_manager is None:
        raise RuntimeError("ExecutionDeps have not been bound")
    return hitl_manager


def _require_room_ownership_reader() -> RoomOwnershipReader:
    if room_ownership_reader is None:
        raise RuntimeError("HITL room ownership dependency has not been bound")
    return room_ownership_reader


async def verify_room_ownership(room_id: str, user: ClerkUser) -> None:
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")

    owner_id = await _require_room_ownership_reader().get_room_owner(room_id)
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


def _raise_http_for_hitl_error(exc: Exception) -> None:
    status_code = _HITL_ERROR_STATUS.get(exc.__class__.__name__, 500)
    message = getattr(exc, "message", str(exc))
    raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/respond")
async def respond_to_hitl_request(
    room_id: str,
    body: HITLResponseRequest,
    user: ClerkUser = Depends(get_current_user),
):
    """User responds to an HITL prompt."""
    await verify_room_ownership(room_id, user)

    try:
        response = await _require_hitl_manager().resolve_hitl(
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
):
    """Get pending HITL requests for a room (SSE reconnect catch-up)."""
    await verify_room_ownership(room_id, user)

    requests = await _require_hitl_manager().get_pending_hitl(room_id)
    return {
        "requests": [
            {
                **r.model_dump(mode="json"),
                "message_id": r.message_id,
            }
            for r in requests
        ]
    }


@router.post("/{request_id}/cancel")
async def cancel_hitl_request(
    room_id: str,
    request_id: str,
    user: ClerkUser = Depends(get_current_user),
):
    """Cancel a pending HITL request."""
    await verify_room_ownership(room_id, user)

    try:
        await _require_hitl_manager().cancel_hitl(room_id, request_id)
    except Exception as exc:
        _raise_http_for_hitl_error(exc)
    return {"status": "canceled"}


_mark_declared_owner(router, __name__)
