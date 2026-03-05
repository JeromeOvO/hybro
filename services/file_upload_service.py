"""Validates and stores user-uploaded files.

Validation pipeline:
1. MIME type check against ALLOWED_MIME_TYPES
2. File size check against settings.max_file_size_mb
3. Magic byte validation (actual content vs declared MIME)
"""

from __future__ import annotations

import io
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from common.utils.logger import get_logger
from database.mongodb import mongodb
from models.file_upload import (
    ALLOWED_MIME_TYPES,
    FileUploadMetadata,
    FileUploadResponse,
)
from config.settings import settings

logger = get_logger(__name__)


class FileUploadService:
    def __init__(self):
        self._s3 = None

    @property
    def s3(self):
        if self._s3 is None:
            from services.s3_service import s3_service

            self._s3 = s3_service
        return self._s3

    @property
    def max_file_size_bytes(self) -> int:
        return settings.max_file_size_mb * 1024 * 1024

    async def upload(
        self,
        file: UploadFile,
        room_id: str,
        user_id: str,
    ) -> FileUploadResponse:
        """Full upload pipeline: validate -> S3 upload -> MongoDB metadata."""
        # 1. Validate MIME type
        if file.content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(415, f"Unsupported file type: {file.content_type}")

        # 2. Read and validate size
        content = await file.read()
        if len(content) > self.max_file_size_bytes:
            raise HTTPException(413, f"File exceeds {settings.max_file_size_mb} MB")

        # 3. Validate magic bytes match declared MIME
        actual_mime = self._detect_mime(content)
        if actual_mime and not self._mime_compatible(file.content_type, actual_mime):
            raise HTTPException(422, "File content doesn't match declared type")

        # 4. Upload to S3
        file_id = uuid4().hex
        s3_key = f"uploads/{room_id}/{file_id}/{file.filename}"
        await self.s3.upload_file(
            file_data=io.BytesIO(content),
            s3_key=s3_key,
            content_type=file.content_type,
            content_length=len(content),
        )

        # 5. Store metadata in MongoDB (compensating delete on failure)
        metadata = FileUploadMetadata(
            file_id=file_id,
            room_id=room_id,
            user_id=user_id,
            s3_key=s3_key,
            mime_type=file.content_type,
            file_name=file.filename or "unnamed",
            size_bytes=len(content),
        )
        try:
            await mongodb.file_uploads_collection.insert_one(
                metadata.model_dump()
            )
        except Exception:
            logger.error(
                "MongoDB insert failed after S3 upload, cleaning up S3 object: %s",
                s3_key,
            )
            await self.s3.delete_file(s3_key)
            raise HTTPException(500, "Failed to store file metadata")

        # 6. Generate presigned URL for immediate use
        presigned_url = await self.s3.generate_presigned_url(s3_key)

        return FileUploadResponse(
            file_id=file_id,
            file_url=presigned_url,
            mime_type=file.content_type,
            file_name=file.filename or "unnamed",
            size_bytes=len(content),
        )

    MAGIC_BYTES = {
        b"\x89PNG": "image/png",
        b"\xff\xd8\xff": "image/jpeg",
        b"GIF87a": "image/gif",
        b"GIF89a": "image/gif",
        b"RIFF": "image/webp",  # RIFF....WEBP (also covers audio/wav RIFF....WAVE)
        b"%PDF": "application/pdf",
        b"PK\x03\x04": "application/zip",  # ZIP, DOCX, XLSX are all PK archives
        b"\xff\xfb": "audio/mpeg",  # MP3 frame sync
        b"\xff\xf3": "audio/mpeg",  # MP3 frame sync (alt)
        b"\xff\xf2": "audio/mpeg",  # MP3 frame sync (alt)
        b"ID3": "audio/mpeg",  # MP3 with ID3 tag
        b"\x1aE\xdf\xa3": "video/webm",  # WebM/Matroska
    }

    # ftyp-based detection for MP4 containers (audio/mp4, video/mp4)
    _FTYP_MARKER = b"ftyp"

    def _detect_mime(self, content: bytes) -> str | None:
        """Detect MIME type from magic bytes. Returns None if unrecognized."""
        for magic, mime in self.MAGIC_BYTES.items():
            if content[: len(magic)] == magic:
                if mime == "image/webp" and content[8:12] != b"WEBP":
                    if content[8:12] == b"WAVE":
                        return "audio/wav"
                    continue
                return mime

        # MP4/M4A container: bytes 4-8 == "ftyp"
        # Both audio/mp4 and video/mp4 share this container format
        if len(content) >= 8 and content[4:8] == self._FTYP_MARKER:
            return "video/mp4"  # generic; _mime_compatible allows audio/mp4 via _MP4_CONTAINER_TYPES

        return None

    _MP4_CONTAINER_TYPES = frozenset({"audio/mp4", "video/mp4"})
    _WEBM_CONTAINER_TYPES = frozenset({"audio/webm", "video/webm"})

    @staticmethod
    def _mime_compatible(declared: str, detected: str) -> bool:
        """Check if declared MIME is compatible with detected.

        Special-cases MP4 containers (audio/mp4 and video/mp4 share ftyp magic),
        WebM containers (audio/webm and video/webm share Matroska magic),
        and ZIP-based Office formats (DOCX/XLSX are PK archives).
        """
        if declared == detected:
            return True
        # MP4 container: audio/mp4 and video/mp4 are both valid ftyp
        if detected == "video/mp4" and declared in FileUploadService._MP4_CONTAINER_TYPES:
            return True
        # WebM/Matroska container: audio/webm and video/webm share the same magic
        if detected == "video/webm" and declared in FileUploadService._WEBM_CONTAINER_TYPES:
            return True
        # ZIP-based formats: DOCX, XLSX, etc. are PK archives
        if detected == "application/zip" and declared.startswith("application/vnd.openxmlformats"):
            return True
        return declared.split("/")[0] == detected.split("/")[0]


file_upload_service = FileUploadService()
