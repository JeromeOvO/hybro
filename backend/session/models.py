"""Durable session record."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from common.utils.time import ensure_utc


def new_session_id() -> str:
    """An opaque, unguessable session handle."""
    return secrets.token_urlsafe(24)


class SessionRecord(BaseModel):
    """One client session with the owner's agents.

    ``conversation_id`` is null until the conversation container exists; a
    send reserves the record first and creates the container second, so a
    retry with the same ``creation_request_id`` completes a crashed creation.
    ``last_send_event_seq`` is the room event sequence read before the last
    accepted send; state resolution pages events after it.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    owner_user_id: str
    client_label: str
    title: str | None = None
    conversation_id: str | None = None
    creation_request_id: str | None = None
    last_client_request_id: str | None = None
    last_user_message_id: str | None = None
    last_accepted_agent_id: str | None = None
    last_send_event_seq: int = 0
    status: Literal["open", "closed"] = "open"
    opened_at: datetime
    last_activity_at: datetime
    closed_at: datetime | None = None

    @field_validator("opened_at", "last_activity_at", "closed_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        # Mongo returns naive datetimes; they are stored as UTC.
        return ensure_utc(value) if value is not None else None


__all__ = ["SessionRecord", "new_session_id"]
