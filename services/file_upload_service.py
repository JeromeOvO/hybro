"""Compatibility shim for the legacy file upload service import path."""

from fastapi import HTTPException, UploadFile

from common.errors import FileStoragePlatformError
from common.protocols import FileStorage
from models.file_upload import FileUploadResponse


class FileUploadService:
    def __init__(self, delegate: FileStorage | None = None) -> None:
        self._delegate = delegate

    def bind(self, delegate: FileStorage) -> None:
        self._delegate = delegate

    def _require_delegate(self) -> FileStorage:
        if self._delegate is None:
            raise RuntimeError("FileUploadService.bind() not called - startup incomplete")
        return self._delegate

    async def upload(
        self,
        file: UploadFile,
        room_id: str,
        user_id: str,
    ) -> FileUploadResponse:
        content = await file.read()
        try:
            result = await self._require_delegate().upload(
                file_bytes=content,
                filename=file.filename or "unnamed",
                owner_id=user_id,
                room_id=room_id,
                content_type=file.content_type,
            )
        except FileStoragePlatformError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc

        return FileUploadResponse(
            file_id=result.file_id,
            file_url=result.url or "",
            mime_type=result.mime_type,
            file_name=result.file_name,
            size_bytes=result.size_bytes,
        )


file_upload_service = FileUploadService()
