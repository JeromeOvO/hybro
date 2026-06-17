from __future__ import annotations

import io
from typing import Any
from urllib.parse import quote

import aioboto3
from botocore.exceptions import ClientError

from common.config import settings
from common.errors import ObjectStorageError

_MISSING_OBJECT_CODES = {"NoSuchKey", "404", "NotFound"}


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
        try:
            await self.put_file(
                key,
                io.BytesIO(data),
                content_type=content_type,
                content_length=len(data),
            )
        except ObjectStorageError:
            raise
        return key

    async def put_file(
        self,
        key: str,
        file_data,
        content_type: str = "",
        content_length: int | None = None,
    ) -> str:
        del content_length
        extra_args = {
            "ContentType": content_type or "application/octet-stream",
        }
        try:
            async with self._session.client("s3", region_name=self._region) as client:
                await client.upload_fileobj(
                    file_data,
                    self._bucket,
                    key,
                    ExtraArgs=extra_args,
                )
        except Exception as exc:
            raise _object_storage_error("upload", key, exc) from exc
        return key

    async def get_presigned_url(
        self, key: str, ttl: int = 3600, filename: str | None = None
    ) -> str:
        params: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
        if filename:
            safe_name = quote(filename, safe="")
            params["ResponseContentDisposition"] = (
                f"attachment; filename*=UTF-8''{safe_name}"
            )
        try:
            async with self._session.client("s3", region_name=self._region) as client:
                return await client.generate_presigned_url(
                    "get_object",
                    Params=params,
                    ExpiresIn=ttl,
                )
        except Exception as exc:
            raise _object_storage_error("presign", key, exc) from exc

    async def get_text(self, key: str) -> str | None:
        try:
            async with self._session.client("s3", region_name=self._region) as client:
                response = await client.get_object(Bucket=self._bucket, Key=key)
                body = response.get("Body")
                if body is None:
                    raise ValueError("S3 get_object response did not include Body")
                data = await body.read()
                return data.decode("utf-8")
        except ClientError as exc:
            if _is_missing_object(exc):
                return None
            raise _object_storage_error("get_text", key, exc) from exc
        except Exception as exc:
            raise _object_storage_error("get_text", key, exc) from exc

    async def delete(self, key: str) -> bool:
        try:
            async with self._session.client("s3", region_name=self._region) as client:
                await client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            raise _object_storage_error("delete", key, exc) from exc
        return True

    async def delete_prefix(self, prefix: str) -> int:
        deleted = 0
        try:
            async with self._session.resource("s3", region_name=self._region) as s3:
                bucket = await s3.Bucket(self._bucket)
                async for obj in bucket.objects.filter(Prefix=prefix):
                    await obj.delete()
                    deleted += 1
        except Exception as exc:
            raise _object_storage_error("delete_prefix", prefix, exc) from exc
        return deleted

    def get_public_url(self, key: str) -> str:
        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"

    async def head(self, key: str) -> dict | None:
        try:
            async with self._session.client("s3", region_name=self._region) as client:
                response = await client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _is_missing_object(exc):
                return None
            raise _object_storage_error("head", key, exc) from exc
        except Exception as exc:
            raise _object_storage_error("head", key, exc) from exc
        return {
            "content_type": response.get("ContentType"),
            "content_length": response.get("ContentLength"),
            "last_modified": response.get("LastModified"),
        }


def _is_missing_object(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    return error.get("Code") in _MISSING_OBJECT_CODES


def _object_storage_error(operation: str, key: str, exc: Exception) -> ObjectStorageError:
    if isinstance(exc, ObjectStorageError):
        return exc
    return ObjectStorageError(
        f"Object storage {operation} failed for {key}",
        details={
            "operation": operation,
            "key": key,
            "error": str(exc),
        },
    )
