"""Shared API Gateway error mapping helpers."""

from __future__ import annotations

from fastapi import HTTPException

SESSION_CONVERSATION_READ_ONLY = (
    "This conversation belongs to a session and is read-only in chat"
)


def require_chat_room(room: object) -> None:
    """Refuse a chat write on a conversation container owned by Session.

    Chat read routes stay open so the owner's session page can render the
    record; chat writes (send, membership, history edits, cancel, upload) are
    refused.
    """
    owner_product = getattr(room, "owner_product", "chat")
    if isinstance(owner_product, str) and owner_product != "chat":
        raise HTTPException(status_code=409, detail=SESSION_CONVERSATION_READ_ONLY)


def internal_server_error(message: str = "Internal server error") -> HTTPException:
    return HTTPException(status_code=500, detail=message)
