from __future__ import annotations

from typing import Any, Protocol

from common.protocols import RoomMemoryCleanupPort
from common.utils.logger import get_logger
from models.request import RoomCenterRoomSettingRequest
from models.response import RoomCenterRoomSettingResponse

logger = get_logger(__name__)


class RoomDeletionLifecycle(Protocol):
    async def get_room_owner(self, room_id: str) -> str | None: ...

    async def cleanup_room_owned_data(self, room_id: str) -> Any: ...

    async def delete_room(self, room_id: str, owner_id: str) -> bool: ...


class RoomFileDeletionLifecycle(Protocol):
    async def begin_room_deletion(self, room_id: str, owner_id: str) -> str | None: ...

    async def wait_for_room_writes(self, room_id: str) -> bool: ...

    async def set_deletion_phase(
        self, room_id: str, deletion_id: str, phase: str
    ) -> bool: ...

    async def delete_for_room(
        self, room_id: str, *, deletion_id: str | None = None
    ) -> int: ...

    async def delete_room_state(self, room_id: str) -> bool: ...


class RoomDeletionService:
    """Coordinates the existing room deletion lifecycle through narrow ports."""

    def __init__(
        self,
        *,
        room_lifecycle: RoomDeletionLifecycle,
        memory_cleanup: RoomMemoryCleanupPort | None,
        file_lifecycle: RoomFileDeletionLifecycle | None = None,
    ) -> None:
        self._room_lifecycle = room_lifecycle
        self._memory_cleanup = memory_cleanup
        self._file_lifecycle = file_lifecycle

    async def delete_room_by_room_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        if request.room_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        actual_owner_id = await self._room_lifecycle.get_room_owner(room_id)
        if actual_owner_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Room not found",
                status_code=404,
            )
        requested_owner_id = (
            request.requesting_user_id or request.room_owner_id or actual_owner_id
        )
        if requested_owner_id != actual_owner_id:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Forbidden",
                status_code=403,
            )

        if self._file_lifecycle is None:
            success = await self._room_lifecycle.delete_room(room_id, actual_owner_id)
            if success:
                await self._cleanup_context_memory_for_room(room_id)
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=bool(success),
                error=None if success else "Failed to delete room",
                status_code=200 if success else 500,
            )

        deletion_id = await self._file_lifecycle.begin_room_deletion(
            room_id, actual_owner_id
        )
        if deletion_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Room deletion could not be started",
                status_code=409,
            )
        if not await self._file_lifecycle.wait_for_room_writes(room_id):
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Room still has active writes",
                status_code=409,
            )

        await self._file_lifecycle.set_deletion_phase(room_id, deletion_id, "cleaning")
        cleanup_ok = await self._cleanup_context_memory_for_room(room_id)
        owned_cleanup_ok = await self._delete_room_owned_data(
            room_id, deletion_id=deletion_id
        )
        if not cleanup_ok or not owned_cleanup_ok:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Room cleanup is incomplete and will be retried",
                status_code=500,
            )

        await self._file_lifecycle.set_deletion_phase(
            room_id, deletion_id, "finalizing"
        )
        success = await self._room_lifecycle.delete_room(room_id, actual_owner_id)
        if not success:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Failed to finalize room deletion",
                status_code=500,
            )
        return RoomCenterRoomSettingResponse(
            room_id=room_id,
            room=None,
            success=True,
            error=None,
            status_code=200,
        )

    async def _cleanup_context_memory_for_room(self, room_id: str) -> bool:
        if self._memory_cleanup is None:
            logger.warning(
                "Context & Memory cleanup skipped for room %s; manager not bound",
                room_id,
            )
            return False
        try:
            ok = await self._memory_cleanup.delete_room_memory(room_id)
            if not ok:
                logger.warning(
                    "Context & Memory cleanup reported failure for room %s",
                    room_id,
                )
            return bool(ok)
        except Exception:
            logger.warning(
                "Context & Memory cleanup failed for room %s",
                room_id,
                exc_info=True,
            )
            return False

    async def _delete_room_owned_data(self, room_id: str, *, deletion_id: str) -> bool:
        file_lifecycle = self._file_lifecycle
        if file_lifecycle is None:
            return True

        ok = True
        try:
            await file_lifecycle.delete_for_room(room_id, deletion_id=deletion_id)
            if not await file_lifecycle.delete_room_state(room_id):
                ok = False
        except Exception:
            ok = False
            logger.warning(
                "Room file cleanup failed for room %s; recovery will retry",
                room_id,
                exc_info=True,
            )

        try:
            await self._room_lifecycle.cleanup_room_owned_data(room_id)
        except Exception:
            ok = False
            logger.warning(
                "Room quotes cleanup failed for room %s",
                room_id,
                exc_info=True,
            )
        return ok
