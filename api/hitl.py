"""HITL (Human-in-the-Loop) REST API endpoints.

Provides endpoints for users to respond to, query, and cancel HITL requests
that are generated when agents or the supervisor need human input.

See docs/HITL_DESIGN.md §7.4 for design details.
"""

from fastapi import APIRouter, Depends, HTTPException

from api.room_center import verify_room_ownership
from common.auth import ClerkUser, get_current_user
from models.hitl import HITLResponseRequest
from services.hitl_service import hitl_service

router = APIRouter(prefix="/rooms/{room_id}/hitl", tags=["hitl"])


@router.post("/respond")
async def respond_to_hitl_request(
    room_id: str,
    body: HITLResponseRequest,
    user: ClerkUser = Depends(get_current_user),
):
    """User responds to an HITL prompt."""
    await verify_room_ownership(room_id, user)

    result = await hitl_service.handle_response(
        room_id=room_id,
        request_id=body.request_id,
        user_input=body.user_input,
        user_id=user.user_id,
    )
    return result


@router.get("/pending")
async def get_pending_hitl_requests(
    room_id: str,
    user: ClerkUser = Depends(get_current_user),
):
    """Get pending HITL requests for a room (SSE reconnect catch-up)."""
    await verify_room_ownership(room_id, user)

    requests = await hitl_service.get_pending_requests(room_id)
    return {"requests": [r.model_dump(mode="json") for r in requests]}


@router.post("/{request_id}/cancel")
async def cancel_hitl_request(
    room_id: str,
    request_id: str,
    user: ClerkUser = Depends(get_current_user),
):
    """Cancel a pending HITL request."""
    await verify_room_ownership(room_id, user)

    await hitl_service.cancel_request(request_id, room_id=room_id)
    return {"status": "canceled"}
