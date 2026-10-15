"""Per-request binding of the session facade to one owner and resolver."""

from __future__ import annotations

from common.protocols import AgentNetworkTargetResolver
from models.session import (
    SessionClosed,
    SessionListResponse,
    SessionMessageRequest,
    SessionMessageResponse,
    SessionOwnerRecord,
    SessionStatus,
)
from session.protocols import SessionOwner, SessionService


class BoundSessionAccess:
    """``SessionAccess`` for one authenticated owner."""

    def __init__(
        self,
        service: SessionService,
        *,
        owner: SessionOwner,
        resolver: AgentNetworkTargetResolver,
    ) -> None:
        self._service = service
        self._owner = owner
        self._resolver = resolver

    async def send(self, request: SessionMessageRequest) -> SessionMessageResponse:
        return await self._service.send(
            request, owner=self._owner, resolver=self._resolver
        )

    async def check(self, session_id: str) -> SessionStatus:
        return await self._service.check(session_id, owner=self._owner)

    async def close(self, session_id: str) -> SessionClosed:
        return await self._service.close(session_id, owner=self._owner)

    async def list_sessions(self) -> SessionListResponse:
        return await self._service.list_for_owner(owner=self._owner)

    async def get_record(self, session_id: str) -> SessionOwnerRecord:
        return await self._service.record_for_owner(session_id, owner=self._owner)


__all__ = ["BoundSessionAccess"]
