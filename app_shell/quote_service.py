"""Create and validate quoted snippets (QUOTE_REPLY Phase 1)."""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from models.quote import (
    MAX_QUOTE_TEXT_LENGTH,
    QuotedSnippet,
    QuoteSourceKind,
    UserQuoteCreatePayload,
)


@runtime_checkable
class QuoteSourceReader(Protocol):
    async def get_room_user_message_by_message_id(self, message_id: str) -> Any | None: ...

    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> Any | None: ...


@runtime_checkable
class QuoteWriter(Protocol):
    async def insert_quoted_snippet(self, snippet: QuotedSnippet) -> str: ...

logger = logging.getLogger(__name__)


class QuoteValidationError(ValueError):
    """Invalid quote payload (HTTP 400)."""


async def validate_quote_source(
    source_reader: QuoteSourceReader,
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
        um = await source_reader.get_room_user_message_by_message_id(mid)
        if not um or um.room_id != room_id:
            raise QuoteValidationError("Invalid quote source (user_turn)")
        return

    # agent | synthesis — both stored as agent messages
    am = await source_reader.get_room_agent_message_by_message_id(mid)
    if not am or am.room_id != room_id:
        raise QuoteValidationError("Invalid quote source (agent/synthesis)")


async def create_quoted_snippet(
    source_reader: QuoteSourceReader,
    *,
    room_id: str,
    created_by_user_id: str,
    payload: UserQuoteCreatePayload,
    writer: QuoteWriter | None = None,
) -> str:
    """Validate, insert snippet, return ``quote_id``."""
    text = payload.text.strip()
    if not text:
        raise QuoteValidationError("Quote text is required")
    if len(text) > MAX_QUOTE_TEXT_LENGTH:
        raise QuoteValidationError(
            f"Quote text exceeds maximum length of {MAX_QUOTE_TEXT_LENGTH} characters"
        )

    await validate_quote_source(source_reader, room_id=room_id, payload=payload)

    snippet_writer = writer or source_reader

    snippet = QuotedSnippet(
        room_id=room_id,
        created_by_user_id=created_by_user_id or "",
        text=text,
        source_message_id=payload.source_message_id.strip(),
        source_kind=str(payload.source_kind.value),
        source_agent_id=payload.source_agent_id,
        sender_display_name=payload.sender_display_name,
    )
    return await snippet_writer.insert_quoted_snippet(snippet)
