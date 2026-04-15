"""Turns API — read-only endpoints for turn event journals.

See spec: docs/superpowers/specs/2026-04-11-room-message-area-redesign.md Section 7.6
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from common.auth import ClerkUser, get_current_user
from services.database_service import db_service

router = APIRouter()


def _flatten_turn_journal(turn_doc: dict) -> dict:
    """Convert persisted turn doc to wire format.

    Persistence stores events with nested {"type": ..., "payload": {...}}.
    Wire format (spec section 4.1) is flat: payload fields promoted to top level.
    """
    from models.turn_event import TurnEvent as TurnEventModel

    flattened_events = []
    for raw_event in turn_doc.get("events", []):
        event = TurnEventModel.from_db({**raw_event, "turn_id": turn_doc["turn_id"]})
        flattened_events.append(event.to_wire())
    return {**turn_doc, "events": flattened_events}


@router.get("/rooms/{room_id}/turns/recent")
async def get_recent_turns(
    room_id: str = Path(..., description="Room ID"),
    limit: int = Query(50, ge=1, le=100),
    user: ClerkUser = Depends(get_current_user),
) -> list[dict]:
    """First-screen load: recent turns with full event journals."""
    turns = await db_service.get_recent_turns(room_id, limit=limit)
    return [_flatten_turn_journal(t) for t in turns]


@router.get("/rooms/{room_id}/turns")
async def get_turns_summary(
    room_id: str = Path(..., description="Room ID"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: ClerkUser = Depends(get_current_user),
) -> list[dict]:
    """Paginated turn summary list (no events, lightweight)."""
    return await db_service.get_turns_summary(room_id, page=page, limit=limit)


@router.get("/rooms/{room_id}/turns/{turn_id}")
async def get_turn_by_id(
    room_id: str = Path(..., description="Room ID"),
    turn_id: str = Path(..., description="Turn ID"),
    user: ClerkUser = Depends(get_current_user),
) -> dict:
    """Single turn with full persisted event journal."""
    turn = await db_service.get_turn_events(room_id, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    return _flatten_turn_journal(turn)
