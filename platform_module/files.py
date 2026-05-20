from __future__ import annotations

from common.dto import FileInfo
from common.errors import FileStoragePlatformError
from platform_module.config import PlatformConfig
from platform_module.deps import PlatformDeps


class PlatformFileStorage:
    MAGIC_BYTES = {
        b"\x89PNG": "image/png",
        b"\xff\xd8\xff": "image/jpeg",
        b"GIF87a": "image/gif",
        b"GIF89a": "image/gif",
        b"RIFF": "image/webp",
        b"%PDF": "application/pdf",
        b"PK\x03\x04": "application/zip",
        b"\xff\xfb": "audio/mpeg",
        b"\xff\xf3": "audio/mpeg",
        b"\xff\xf2": "audio/mpeg",
        b"ID3": "audio/mpeg",
        b"\x1aE\xdf\xa3": "video/webm",
    }
    _FTYP_MARKER = b"ftyp"
    _MP4_CONTAINER_TYPES = frozenset({"audio/mp4", "video/mp4"})
    _WEBM_CONTAINER_TYPES = frozenset({"audio/webm", "video/webm"})

    def __init__(self, config: PlatformConfig, deps: PlatformDeps) -> None:
        self._config = config
        self._deps = deps

    async def upload(
        self,
        file_bytes: bytes,
        filename: str,
        owner_id: str,
        room_id: str,
        content_type: str | None = None,
    ) -> FileInfo:
        if content_type not in set(self._config.allowed_mime_types):
            raise FileStoragePlatformError(
                415, f"Unsupported file type: {content_type}"
            )

        if len(file_bytes) > self._config.max_upload_size_bytes:
            max_mb = self._config.max_upload_size_bytes // (1024 * 1024)
            raise FileStoragePlatformError(413, f"File exceeds {max_mb} MB")

        actual_mime = self._detect_mime(file_bytes)
        if actual_mime and not self._mime_compatible(content_type, actual_mime):
            raise FileStoragePlatformError(
                422, "File content doesn't match declared type"
            )

        object_storage = self._require_object_storage()
        metadata_repository = self._require_metadata_repository()
        file_id = self._deps.file_id_factory()
        file_name = filename or "unnamed"
        s3_key = f"uploads/{room_id}/{file_id}/{file_name}"

        await object_storage.put(s3_key, file_bytes, content_type)

        metadata = {
            "file_id": file_id,
            "room_id": room_id,
            "user_id": owner_id,
            "s3_key": s3_key,
            "mime_type": content_type,
            "file_name": file_name,
            "size_bytes": len(file_bytes),
            "uploaded_at": self._deps.clock(),
        }
        try:
            await metadata_repository.create(metadata)
        except Exception as exc:
            if self._deps.logger is not None:
                self._deps.logger.error(
                    "File metadata write failed after object upload: %s", s3_key
                )
            await object_storage.delete(s3_key)
            raise FileStoragePlatformError(
                500, "Failed to store file metadata"
            ) from exc

        url = await object_storage.get_presigned_url(
            s3_key, ttl=self._config.presigned_url_ttl_seconds
        )
        return self._to_file_info(metadata, url=url)

    async def get_url(self, file_id: str, ttl: int = 3600) -> str | None:
        metadata = await self._require_metadata_repository().get(file_id)
        if metadata is None:
            return None
        return await self._require_object_storage().get_presigned_url(
            metadata["s3_key"], ttl=ttl
        )

    async def delete(self, file_id: str) -> bool:
        metadata_repository = self._require_metadata_repository()
        metadata = await metadata_repository.get(file_id)
        if metadata is None:
            return False
        await self._require_object_storage().delete(metadata["s3_key"])
        return await metadata_repository.delete(file_id)

    async def list_for_room(self, room_id: str) -> list[FileInfo]:
        records = await self._require_metadata_repository().list_for_room(room_id)
        return [self._to_file_info(record) for record in records]

    def _detect_mime(self, content: bytes) -> str | None:
        for magic, mime in self.MAGIC_BYTES.items():
            if content[: len(magic)] != magic:
                continue
            if mime == "image/webp" and content[8:12] != b"WEBP":
                if content[8:12] == b"WAVE":
                    return "audio/wav"
                continue
            return mime

        if len(content) >= 8 and content[4:8] == self._FTYP_MARKER:
            return "video/mp4"
        return None

    @staticmethod
    def _mime_compatible(declared: str, detected: str) -> bool:
        if declared == detected:
            return True
        if (
            detected == "video/mp4"
            and declared in PlatformFileStorage._MP4_CONTAINER_TYPES
        ):
            return True
        if (
            detected == "video/webm"
            and declared in PlatformFileStorage._WEBM_CONTAINER_TYPES
        ):
            return True
        if detected == "application/zip" and declared.startswith(
            "application/vnd.openxmlformats"
        ):
            return True
        return declared.split("/")[0] == detected.split("/")[0]

    def _require_object_storage(self):
        if self._deps.object_storage is None:
            raise RuntimeError("Platform file storage requires object_storage")
        return self._deps.object_storage

    def _require_metadata_repository(self):
        if self._deps.file_metadata_repository is None:
            raise RuntimeError(
                "Platform file storage requires file_metadata_repository"
            )
        return self._deps.file_metadata_repository

    @staticmethod
    def _to_file_info(record: dict, *, url: str | None = None) -> FileInfo:
        return FileInfo(
            file_id=record["file_id"],
            file_name=record["file_name"],
            mime_type=record["mime_type"],
            size_bytes=record["size_bytes"],
            url=url,
        )


__all__ = ["PlatformFileStorage"]
