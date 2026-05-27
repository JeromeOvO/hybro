"""Quoted snippet models — persisted quote snapshots (QUOTE_REPLY design)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from common.utils.time import utcnow
from pydantic import BaseModel, Field


MAX_QUOTE_TEXT_LENGTH = 8000


class QuoteSourceKind(StrEnum):
    """Provenance for a quote; dispatch uses snapshot text, not re-resolution."""

    AGENT = "agent"
    SYNTHESIS = "synthesis"
    USER_TURN = "user_turn"
    UNKNOWN = "unknown"


class UserQuoteCreatePayload(BaseModel):
    """Inbound quote payload on send (not stored on user message row)."""

    text: str
    source_message_id: str
    source_kind: QuoteSourceKind = QuoteSourceKind.UNKNOWN
    sender_display_name: str | None = None
    source_agent_id: str | None = None


class QuotedSnippet(BaseModel):
    """Persisted immutable quote snapshot (Mongo ``room_quotes``)."""

    quote_id: str = Field(default_factory=lambda: uuid4().hex)
    room_id: str
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utcnow)
    text: str
    format: str = "plain"
    source_message_id: str
    source_kind: str
    source_agent_id: str | None = None
    sender_display_name: str | None = None
