"""Compatibility shim for object storage.

The app-shell import path remains available for legacy runtime bindings, but
the implementation delegates to a bound platform object-storage adapter.
"""

from __future__ import annotations

from typing import BinaryIO, Any as ObjectStoragePort


class S3Service:
    def __init__(self, delegate: ObjectStoragePort | None = None) -> None:
        self._delegate = delegate

    def bind_object_storage(self, delegate: ObjectStoragePort) -> None:
        self._delegate = delegate

    async def upload_file(
        self,
        file_data: BinaryIO | bytes,
        s3_key: str,
        content_type: str,
        content_length: int | None = None,
    ) -> str:
        return await self._require_delegate().upload_file(
            file_data,
            s3_key,
            content_type,
            content_length=content_length,
        )

    async def generate_presigned_url(
        self,
        s3_key: str,
        *,
        filename: str | None = None,
        expires_in: int | None = None,
    ) -> str:
        return await self._require_delegate().generate_presigned_url(
            s3_key,
            filename=filename,
            expires_in=expires_in,
        )

    async def batch_presigned_urls(
        self,
        s3_keys: list[str],
        *,
        filenames: dict[str, str] | None = None,
        expires_in: int | None = None,
    ) -> dict[str, str]:
        return await self._require_delegate().batch_presigned_urls(
            s3_keys,
            filenames=filenames,
            expires_in=expires_in,
        )

    async def delete_file(self, s3_key: str) -> bool:
        return await self._require_delegate().delete_file(s3_key)

    async def head_file(self, s3_key: str) -> dict | None:
        return await self._require_delegate().head_file(s3_key)

    async def delete_prefix(self, prefix: str) -> int:
        return await self._require_delegate().delete_prefix(prefix)

    def get_public_url(self, s3_key: str) -> str:
        return self._require_delegate().get_public_url(s3_key)

    async def download_text(self, s3_key: str) -> str | None:
        return await self._require_delegate().download_text(s3_key)

    async def get_text(self, s3_key: str) -> str | None:
        return await self.download_text(s3_key)

    def _require_delegate(self) -> ObjectStoragePort:
        if self._delegate is None:
            raise RuntimeError(
                "S3Service.bind_object_storage() not called - startup incomplete"
            )
        return self._delegate


s3_service = S3Service()
