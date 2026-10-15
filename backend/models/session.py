"""Public payloads for client sessions with the owner's agents.

A session is a conversation that a client of the instance owner opens through
the API or MCP. The client addresses one agent per message; the owner approves
or rejects agent interactions in the Hybro app. The client never sees the
conversation container id or the approval question.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from common.idempotency import MAX_CLIENT_REQUEST_ID_LENGTH
from models.room import MAX_MESSAGE_LENGTH

SessionState = Literal[
    "completed", "working", "waiting_on_owner", "rejected", "failed", "closed"
]

SessionIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
ClientRequestId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_CLIENT_REQUEST_ID_LENGTH
    ),
]
ClientLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
SessionText = Annotated[str, StringConstraints(max_length=MAX_MESSAGE_LENGTH)]


class SessionMessageRequest(BaseModel):
    """One client message to one agent, opening a session when no handle is given."""

    model_config = ConfigDict(extra="forbid")

    session_id: SessionIdentifier | None = None
    agent_id: SessionIdentifier
    text: SessionText
    subnet_id: SessionIdentifier | None = None
    client_request_id: ClientRequestId
    client_label: ClientLabel | None = None

    @model_validator(mode="after")
    def _require_text_and_label(self) -> Self:
        if not self.text.strip():
            raise ValueError("text must not be blank")
        if self.session_id is None and self.client_label is None:
            raise ValueError("client_label is required to open a session")
        return self


class SessionAgent(BaseModel):
    agent_id: str
    name: str | None = None


class SessionReply(BaseModel):
    message_id: str
    text: str
    created_at: datetime | None = None


class SessionMessageResponse(BaseModel):
    session_id: str
    accepted: bool
    state: SessionState
    reply: str | None = None
    paused_agent_id: str | None = None
    reason: str | None = None
    client_request_id: str


class SessionStatus(BaseModel):
    """The client's view of a session. It never carries the container id."""

    session_id: str
    state: SessionState
    latest_replies: list[SessionReply] = Field(default_factory=list)
    paused_agent_id: str | None = None
    reason: str | None = None
    agents: list[SessionAgent] = Field(default_factory=list)
    opened_at: datetime
    last_activity_at: datetime


class SessionClosed(BaseModel):
    session_id: str
    status: Literal["open", "closed"]


class SessionSummary(BaseModel):
    """One row of the owner's session list."""

    session_id: str
    client_label: str
    title: str | None = None
    agents: list[SessionAgent] = Field(default_factory=list)
    state: SessionState
    paused_agent_id: str | None = None
    conversation_id: str | None = None
    opened_at: datetime
    last_activity_at: datetime
    closed_at: datetime | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary] = Field(default_factory=list)


class SessionOwnerRecord(SessionStatus):
    """The owner's view: the client fields plus the conversation container."""

    client_label: str
    title: str | None = None
    status: Literal["open", "closed"]
    conversation_id: str | None = None
    closed_at: datetime | None = None


class SessionErrorDetail(BaseModel):
    code: str
    message: str
    session_id: str | None = None
    client_request_id: str | None = None


class SessionErrorResponse(BaseModel):
    detail: SessionErrorDetail


__all__ = [
    "SessionAgent",
    "SessionClosed",
    "SessionErrorDetail",
    "SessionErrorResponse",
    "SessionListResponse",
    "SessionMessageRequest",
    "SessionMessageResponse",
    "SessionOwnerRecord",
    "SessionReply",
    "SessionState",
    "SessionStatus",
    "SessionSummary",
]
