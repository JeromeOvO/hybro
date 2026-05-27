"""Resolve the root turn_id for a RoomAgentMessage.

Prefers the persisted turn_id field. Falls back to walking the
related_message_id chain to the root user message. Only needed
during migration when old messages lack turn_id.

See spec: docs/superpowers/specs/2026-04-11-room-message-area-redesign.md Section 4.5
"""

from __future__ import annotations

from typing import Protocol


class TurnMessage(Protocol):
    message_id: str
    related_message_id: str | None
    turn_id: str | None


async def resolve_turn_id(msg: TurnMessage, db_service) -> str:
    if msg.turn_id:
        return msg.turn_id

    current_id = msg.related_message_id
    visited: set[str] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        user_msg = await db_service.get_room_user_message_by_message_id(current_id)
        if user_msg:
            return current_id

        agent_msg = await db_service.get_room_agent_message_by_message_id(current_id)
        if not agent_msg or not agent_msg.related_message_id:
            return current_id
        current_id = agent_msg.related_message_id

    return msg.message_id
