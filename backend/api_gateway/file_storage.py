"""API Gateway file upload storage backed by object storage metadata."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import datetime
from typing import Any
from uuid import uuid4

from common.dto import FileInfo
from common.errors import FileStoragePlatformError, ObjectStorageError
from common.protocols import ObjectStorageDAL
from common.utils.logger import get_logger
from common.utils.time import utcnow

DEFAULT_MIME_TYPE = "application/octet-stream"
DEFAULT_PRESIGNED_URL_TTL = 3600

logger = get_logger(__name__)


class ObjectStorageFileStorage:
    def __init__(
        self,
        *,
        object_storage: ObjectStorageDAL,
        file_uploads_collection: Any,
        file_id_factory: Callable[[], str] | None = None,
        now: Callable[[], datetime] = utcnow,
        presigned_url_ttl: int = DEFAULT_PRESIGNED_URL_TTL,
        max_upload_bytes: int | None = None,
    ) -> None:
        self._object_storage = object_storage
        self._file_uploads_collection = file_uploads_collection
        self._file_id_factory = file_id_factory or (lambda: uuid4().hex)
        self._now = now
        self._presigned_url_ttl = presigned_url_ttl
        self._max_upload_bytes = (
            max(1, int(max_upload_bytes)) if max_upload_bytes is not None else None
        )

    async def upload(
        self,
        file_bytes: bytes,
        filename: str,
        owner_id: str,
        room_id: str,
        content_type: str | None = None,
    ) -> FileInfo:
        if (
            self._max_upload_bytes is not None
            and len(file_bytes) > self._max_upload_bytes
        ):
            raise FileStoragePlatformError(
                detail=(
                    "Uploaded file exceeds the maximum upload size "
                    f"({len(file_bytes)} > {self._max_upload_bytes} bytes)."
                ),
                status_code=413,
            )

        file_id = self._file_id_factory()
        safe_name = _sanitize_filename(filename)
        mime_type = content_type or DEFAULT_MIME_TYPE
        s3_key = _build_upload_key(room_id=room_id, file_id=file_id, filename=safe_name)

        try:
            await self._object_storage.put(s3_key, file_bytes, content_type=mime_type)
            await self._file_uploads_collection.insert_one(
                {
                    "file_id": file_id,
                    "room_id": room_id,
                    "user_id": owner_id,
                    "s3_key": s3_key,
                    "mime_type": mime_type,
                    "file_name": safe_name,
                    "size_bytes": len(file_bytes),
                    "uploaded_at": self._now(),
                }
            )
            url = await self._object_storage.get_presigned_url(
                s3_key,
                ttl=self._presigned_url_ttl,
                filename=safe_name,
            )
        except Exception as exc:
            _raise_file_storage_error("upload", exc)

        return FileInfo(
            file_id=file_id,
            file_name=safe_name,
            mime_type=mime_type,
            size_bytes=len(file_bytes),
            url=url,
        )

    async def get_url(self, file_id: str, ttl: int = 3600) -> str | None:
        try:
            doc = await self._file_uploads_collection.find_one({"file_id": file_id})
            if doc is None:
                return None
            return await self._object_storage.get_presigned_url(
                str(doc["s3_key"]),
                ttl=ttl,
                filename=doc.get("file_name"),
            )
        except Exception as exc:
            _raise_file_storage_error("get_url", exc)

    async def delete(self, file_id: str) -> bool:
        try:
            doc = await self._file_uploads_collection.find_one({"file_id": file_id})
            if doc is None:
                return False
            storage_deleted = await self._object_storage.delete(str(doc["s3_key"]))
            if not storage_deleted:
                return False
            result = await self._file_uploads_collection.delete_one(
                {"file_id": file_id}
            )
            return bool(getattr(result, "deleted_count", 0))
        except Exception as exc:
            _raise_file_storage_error("delete", exc)

    async def list_for_room(self, room_id: str) -> list[FileInfo]:
        try:
            cursor = self._file_uploads_collection.find({"room_id": room_id})
            if inspect.isawaitable(cursor):
                cursor = await cursor
            return [_file_info_from_doc(doc) async for doc in _aiter_documents(cursor)]
        except Exception as exc:
            _raise_file_storage_error("list_for_room", exc)

    async def get_bytes(self, key: str, *, max_bytes: int) -> bytes | None:
        try:
            return await self._object_storage.get_bytes(key, max_bytes=max_bytes)
        except ObjectStorageError:
            raise
        except Exception as exc:
            _raise_file_storage_error("get_bytes", exc)

    async def get_for_room_file(
        self, room_id: str, file_id: str
    ) -> dict[str, Any] | None:
        try:
            doc = await self._file_uploads_collection.find_one(
                {"room_id": room_id, "file_id": file_id}
            )
        except Exception as exc:
            _raise_file_storage_error("get_for_room_file", exc)
        return dict(doc) if doc is not None else None


def _sanitize_filename(filename: str) -> str:
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return safe_name or "unnamed"


def _build_upload_key(*, room_id: str, file_id: str, filename: str) -> str:
    return f"uploads/{room_id}/{file_id}/{filename}"


def _file_info_from_doc(doc: dict[str, Any]) -> FileInfo:
    return FileInfo(
        file_id=str(doc["file_id"]),
        file_name=str(doc["file_name"]),
        mime_type=str(doc["mime_type"]),
        size_bytes=int(doc["size_bytes"]),
        url=None,
    )


async def _aiter_documents(cursor: AsyncIterator | Iterable):
    if hasattr(cursor, "__aiter__"):
        async for doc in cursor:
            yield doc
        return
    for doc in cursor:
        yield doc


def _raise_file_storage_error(operation: str, exc: Exception) -> None:
    if isinstance(exc, FileStoragePlatformError):
        raise exc
    logger.exception("File storage %s failed: %s", operation, exc)
    raise FileStoragePlatformError(
        500,
        {"message": f"File storage {operation} failed"},
    ) from exc
