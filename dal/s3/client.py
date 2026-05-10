from __future__ import annotations

import io
from typing import Any

import aioboto3

from common.config import settings


class ObjectStorageDALImpl:
    """Object storage DAL backed directly by aioboto3."""

    def __init__(
        self,
        *,
        session: Any | None = None,
        bucket: str | None = None,
        region: str | None = None,
    ) -> None:
        self._region = region or settings.s3_region
        self._bucket = bucket if bucket is not None else settings.s3_bucket_name
        self._session = session or aioboto3.Session(
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            region_name=self._region,
        )

    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        extra_args = {
            "ContentType": content_type or "application/octet-stream",
        }
        async with self._session.client("s3", region_name=self._region) as client:
            await client.upload_fileobj(
                io.BytesIO(data),
                self._bucket,
                key,
                ExtraArgs=extra_args,
            )
        return key

    async def get_presigned_url(self, key: str, ttl: int = 3600) -> str:
        async with self._session.client("s3", region_name=self._region) as client:
            return await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=ttl,
            )

    async def delete(self, key: str) -> bool:
        async with self._session.client("s3", region_name=self._region) as client:
            await client.delete_object(Bucket=self._bucket, Key=key)
        return True
