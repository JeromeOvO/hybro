from __future__ import annotations

from common.protocols import AttachmentCleanupPort, AttachmentMetadataReader


class PlatformAttachmentMetadataReader(AttachmentMetadataReader):
    def __init__(self, file_metadata_repository) -> None:
        self._files = file_metadata_repository

    async def get_for_room_file(self, room_id: str, file_id: str) -> dict | None:
        doc = await self._files.get(file_id)
        if doc is None or doc.get("room_id") != room_id:
            return None
        return doc


class PlatformAttachmentCleanupPort(AttachmentCleanupPort):
    def __init__(self, file_metadata_repository) -> None:
        self._files = file_metadata_repository

    async def delete_for_room(self, room_id: str) -> int:
        return await self._files.delete_for_room(room_id)
