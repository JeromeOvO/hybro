"""The room-lifecycle port that ``room_files`` consumes.

Conversation owns the ``rooms`` collection and implements this port
(``conversation.repository.mongo.RoomMongoRepository``). ``room_files`` uses it
for write leases, deletion fencing, and deletion recovery, and never touches the
collection itself.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class RoomLifecycleStore(Protocol):
    async def get_active_room_owner(self, conversation_id: str) -> str | None:
        """Return the owner of an active room, or ``None``."""
        ...

    async def acquire_write_lease(
        self,
        conversation_id: str,
        *,
        lease_id: str,
        owner: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """Drop leases expired at ``now``, then atomically add one to an active
        room; return whether the stored room now holds ``lease_id``."""
        ...

    async def renew_write_lease(
        self, conversation_id: str, lease_id: str, *, expires_at: datetime
    ) -> bool: ...

    async def release_write_lease(
        self, conversation_id: str, lease_id: str
    ) -> None: ...

    async def has_valid_write_lease(
        self, conversation_id: str, lease_id: str, *, now: datetime
    ) -> bool:
        """Return whether an active room holds ``lease_id`` unexpired at ``now``."""
        ...

    async def has_live_write_leases(
        self, conversation_id: str, *, now: datetime
    ) -> bool:
        """Drop leases expired at ``now``; return whether any lease remains."""
        ...

    async def begin_deletion(
        self,
        conversation_id: str,
        owner_id: str,
        *,
        deletion_id: str,
        started_at: datetime,
    ) -> str | None:
        """Fence an active room as deleting, or return the deletion id of an
        owner's room already deleting; ``None`` if neither applies."""
        ...

    async def set_deletion_phase(
        self, conversation_id: str, deletion_id: str, phase: str
    ) -> bool: ...

    async def list_stale_deletions(
        self, *, started_before: datetime
    ) -> list[tuple[str, str]]:
        """Return ``(conversation_id, deletion_id)`` of rooms deleting since
        before ``started_before``."""
        ...

    async def delete_room_in_deletion(
        self, conversation_id: str, deletion_id: str
    ) -> bool:
        """Delete the room only while it is still in deletion ``deletion_id``."""
        ...


__all__ = ["RoomLifecycleStore"]
