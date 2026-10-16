"""Session ports.

``SessionAccess`` is the route-facing surface bound to one owner per request;
every parameter and return is a public DTO from ``models.session``.
``SessionService`` is the facade surface the gateway holds, ``SessionStore``
the durable record, and ``ConversationEventsPort`` the durable room event log
the bounded wait reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from common.protocols import AgentNetworkTargetResolver
from models.session import (
    SessionClosed,
    SessionListResponse,
    SessionMessageRequest,
    SessionMessageResponse,
    SessionOwnerRecord,
    SessionStatus,
)
from session.models import SessionRecord


@dataclass(frozen=True, slots=True)
class SessionOwner:
    """The instance owner a request acts for."""

    user_id: str
    name: str


@runtime_checkable
class SessionAccess(Protocol):
    async def send(self, request: SessionMessageRequest) -> SessionMessageResponse: ...
    async def check(self, session_id: str) -> SessionStatus: ...
    async def close(self, session_id: str) -> SessionClosed: ...
    async def list_sessions(self) -> SessionListResponse: ...
    async def get_record(self, session_id: str) -> SessionOwnerRecord: ...


@runtime_checkable
class SessionService(Protocol):
    async def send(
        self,
        request: SessionMessageRequest,
        *,
        owner: SessionOwner,
        resolver: AgentNetworkTargetResolver,
    ) -> SessionMessageResponse: ...
    async def check(self, session_id: str, *, owner: SessionOwner) -> SessionStatus: ...
    async def close(self, session_id: str, *, owner: SessionOwner) -> SessionClosed: ...
    async def list_for_owner(self, *, owner: SessionOwner) -> SessionListResponse: ...
    async def record_for_owner(
        self, session_id: str, *, owner: SessionOwner
    ) -> SessionOwnerRecord: ...


@runtime_checkable
class SessionStore(Protocol):
    async def reserve(self, record: SessionRecord) -> SessionRecord: ...
    async def get(self, session_id: str) -> SessionRecord | None: ...
    async def set_conversation_if_unset(
        self, session_id: str, conversation_id: str
    ) -> SessionRecord | None: ...
    async def record_send(
        self,
        session_id: str,
        *,
        client_request_id: str,
        user_message_id: str,
        event_seq: int,
        at: datetime,
    ) -> SessionRecord | None: ...
    async def record_accepted_agent(
        self, session_id: str, agent_id: str
    ) -> SessionRecord | None: ...
    async def mark_closed(
        self, session_id: str, at: datetime
    ) -> SessionRecord | None: ...
    async def list_for_owner(
        self, owner_user_id: str, limit: int = 100
    ) -> list[SessionRecord]: ...
    async def list_open_conversations(self, owner_user_id: str) -> dict[str, str]: ...


@runtime_checkable
class ConversationEventsPort(Protocol):
    async def latest_seq(self, room_id: str) -> int: ...
    async def read_range(
        self, room_id: str, *, after: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]: ...


__all__ = [
    "ConversationEventsPort",
    "SessionAccess",
    "SessionOwner",
    "SessionService",
    "SessionStore",
]
