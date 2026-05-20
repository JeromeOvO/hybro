"""Validates and stores user-uploaded files.

Validation pipeline:
1. MIME type check against ALLOWED_MIME_TYPES
2. File size check against settings.max_file_size_mb
3. Magic byte validation (actual content vs declared MIME)
"""

from __future__ import annotations

import io
from types import SimpleNamespace

from fastapi import HTTPException, UploadFile

from common.errors import FileStoragePlatformError
from common.utils.logger import get_logger
from models.file_upload import (
    ALLOWED_MIME_TYPES,
    FileUploadResponse,
)
from platform_module import PlatformConfig, PlatformDeps
from platform_module.files import PlatformFileStorage

logger = get_logger(__name__)
mongodb = None
settings = SimpleNamespace(max_file_size_mb=50, s3_presigned_url_ttl=3600)


class _LegacyS3ObjectStorage:
    def __init__(self, service: "FileUploadService") -> None:
        self._service = service

    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        return await self._service.s3.upload_file(
            file_data=io.BytesIO(data),
            s3_key=key,
            content_type=content_type,
            content_length=len(data),
        )

    async def get_presigned_url(self, key: str, ttl: int = 3600) -> str:
        return await self._service.s3.generate_presigned_url(key)

    async def delete(self, key: str) -> bool:
        return await self._service.s3.delete_file(key)


class _MongoFileMetadataRepository:
    def _require_delegate(self):
        if mongodb is None:
            raise RuntimeError(
                "FileUploadService metadata repository has not been bound"
            )
        return mongodb.file_uploads_collection

    async def create(self, data: dict) -> str:
        await self._require_delegate().insert_one(data)
        return data["file_id"]

    async def get(self, file_id: str) -> dict | None:
        return await self._require_delegate().find_one({"file_id": file_id})

    async def delete(self, file_id: str) -> bool:
        result = await self._require_delegate().delete_one({"file_id": file_id})
        return bool(getattr(result, "deleted_count", result))

    async def list_for_room(self, room_id: str) -> list[dict]:
        cursor = self._require_delegate().find({"room_id": room_id})
        if hasattr(cursor, "to_list"):
            return await cursor.to_list(length=None)
        return list(cursor)


class FileUploadService:
    def __init__(self):
        self._s3 = None
        self._mime_detector = PlatformFileStorage(PlatformConfig(), PlatformDeps())

    @property
    def s3(self):
        if self._s3 is None:
            raise RuntimeError("FileUploadService S3 dependency has not been bound")
        return self._s3

    def _require_delegate(self) -> None:
        if self._s3 is None or mongodb is None:
            raise RuntimeError("FileUploadService dependencies have not been bound")

    @property
    def max_file_size_bytes(self) -> int:
        return settings.max_file_size_mb * 1024 * 1024

    async def upload(
        self,
        file: UploadFile,
        room_id: str,
        user_id: str,
    ) -> FileUploadResponse:
        """Compatibility shim for the Platform file-storage implementation."""
        content = await file.read()
        platform_storage = PlatformFileStorage(
            config=PlatformConfig(
                max_upload_size_bytes=self.max_file_size_bytes,
                allowed_mime_types=tuple(sorted(ALLOWED_MIME_TYPES)),
                presigned_url_ttl_seconds=settings.s3_presigned_url_ttl,
            ),
            deps=PlatformDeps(
                object_storage=_LegacyS3ObjectStorage(self),
                file_metadata_repository=_MongoFileMetadataRepository(),
                logger=logger,
            ),
        )
        try:
            result = await platform_storage.upload(
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

    def _detect_mime(self, content: bytes) -> str | None:
        return self._mime_detector._detect_mime(content)

    @staticmethod
    def _mime_compatible(declared: str, detected: str) -> bool:
        return PlatformFileStorage._mime_compatible(declared, detected)


file_upload_service = FileUploadService()
