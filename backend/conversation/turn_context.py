"""Turn-scoped context for orchestration and agent dispatch (QUOTE_REPLY)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from models.quote import QuotedSnippet
from models.room import RoomUserMessage

logger = logging.getLogger(__name__)


class QuotedSnippetReader(Protocol):
    async def get_quoted_snippet_by_id(self, quote_id: str) -> QuotedSnippet | None: ...


class TurnQuoteMissingError(RuntimeError):
    """``quote_id`` set on user message but snippet row missing (fail closed §8.9)."""


@dataclass(frozen=True)
class TurnContext:
    """Bundle loaded once per turn (including resume)."""

    user_message_id: str
    room_id: str
    message_text: str
    quote_id: str | None
    quoted_text: str | None
    quoted_sender_display_name: str | None
    quoted_source_message_id: str | None
    quoted_source_kind: str | None


async def load_turn_context(
    db: QuotedSnippetReader,
    user_message: RoomUserMessage,
) -> TurnContext:
    """Resolve ``quote_id`` → DB snippet; else legacy ``extend_info.quoted_text``.

    If ``quote_id`` is set but snippet missing → :class:`TurnQuoteMissingError`.
    If both snippet and inline differ → snippet wins (warning log).
    """
    uid = user_message.message_id
    room_id = user_message.room_id
    raw_text = (
        user_message.message_content.message_text
        if user_message.message_content
        else ""
    )
    message_text = (raw_text or "").strip()

    ext = user_message.extend_info if isinstance(user_message.extend_info, dict) else {}
    legacy_quoted = ext.get("quoted_text")
    legacy_sender = ext.get("quoted_sender_name")
    legacy_quote_id = ext.get("quote_id")

    quote_id = getattr(user_message, "quote_id", None) or legacy_quote_id
    snippet: QuotedSnippet | None = None
    if quote_id:
        snippet = await db.get_quoted_snippet_by_id(quote_id)
        if snippet is None:
            raise TurnQuoteMissingError(
                f"Quoted snippet not found for quote_id={quote_id} user_message={uid}"
            )
        if (
            isinstance(legacy_quoted, str)
            and legacy_quoted
            and legacy_quoted != snippet.text
        ):
            logger.warning(
                "quote dual-write mismatch: snippet wins room=%s user_msg=%s quote_id=%s",
                room_id,
                uid,
                quote_id,
            )

    if snippet is not None:
        return TurnContext(
            user_message_id=uid,
            room_id=room_id,
            message_text=message_text,
            quote_id=snippet.quote_id,
            quoted_text=snippet.text,
            quoted_sender_display_name=snippet.sender_display_name,
            quoted_source_message_id=snippet.source_message_id,
            quoted_source_kind=snippet.source_kind,
        )

    if isinstance(legacy_quoted, str) and legacy_quoted.strip():
        return TurnContext(
            user_message_id=uid,
            room_id=room_id,
            message_text=message_text,
            quote_id=None,
            quoted_text=legacy_quoted.strip(),
            quoted_sender_display_name=(
                legacy_sender if isinstance(legacy_sender, str) else None
            ),
            quoted_source_message_id=None,
            quoted_source_kind=None,
        )

    return TurnContext(
        user_message_id=uid,
        room_id=room_id,
        message_text=message_text,
        quote_id=None,
        quoted_text=None,
        quoted_sender_display_name=None,
        quoted_source_message_id=None,
        quoted_source_kind=None,
    )


def format_quoted_context_header(turn: TurnContext) -> str:
    """First lines of ``[Quoted context]`` block (provenance line)."""
    if not turn.quoted_text:
        return ""
    sender = turn.quoted_sender_display_name or "Unknown"
    sk = turn.quoted_source_kind or "unknown"
    smid = turn.quoted_source_message_id or "n/a"
    return (
        f"The user highlighted the following from {sender}\n"
        f"(source: {sk}, message {smid}):\n"
    )
