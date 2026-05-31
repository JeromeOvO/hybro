"""Create and validate quoted snippets (QUOTE_REPLY Phase 1)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from models.quote import (
    MAX_QUOTE_TEXT_LENGTH,
    QuotedSnippet,
    QuoteSourceKind,
    UserQuoteCreatePayload,
)

if TYPE_CHECKING:
    from services.database_service import DatabaseService

logger = logging.getLogger(__name__)


class QuoteValidationError(ValueError):
    """Invalid quote payload (HTTP 400)."""


async def validate_quote_source(
    db: DatabaseService,
    *,
    room_id: str,
    payload: UserQuoteCreatePayload,
) -> None:
    """Ensure ``source_message_id`` resolves in ``room_id`` (§8.8)."""
    kind = payload.source_kind
    mid = payload.source_message_id.strip()
    if not mid:
        raise QuoteValidationError("source_message_id is required")

    if kind == QuoteSourceKind.UNKNOWN:
        return

    if kind == QuoteSourceKind.USER_TURN:
        um = await db.get_room_user_message_by_message_id(mid)
        if not um or um.room_id != room_id:
            raise QuoteValidationError("Invalid quote source (user_turn)")
        return

    # agent | synthesis — both stored as agent messages
    am = await db.get_room_agent_message_by_message_id(mid)
    if not am or am.room_id != room_id:
        raise QuoteValidationError("Invalid quote source (agent/synthesis)")


async def create_quoted_snippet(
    db: DatabaseService,
    *,
    room_id: str,
    created_by_user_id: str,
    payload: UserQuoteCreatePayload,
) -> str:
    """Validate, insert snippet, return ``quote_id``."""
    text = payload.text.strip()
    if not text:
        raise QuoteValidationError("Quote text is required")
    if len(text) > MAX_QUOTE_TEXT_LENGTH:
        raise QuoteValidationError(
            f"Quote text exceeds maximum length of {MAX_QUOTE_TEXT_LENGTH} characters"
        )

    await validate_quote_source(db, room_id=room_id, payload=payload)

    snippet = QuotedSnippet(
        room_id=room_id,
        created_by_user_id=created_by_user_id or "",
        text=text,
        source_message_id=payload.source_message_id.strip(),
        source_kind=str(payload.source_kind.value),
        source_agent_id=payload.source_agent_id,
        sender_display_name=payload.sender_display_name,
    )
    return await db.insert_quoted_snippet(snippet)
