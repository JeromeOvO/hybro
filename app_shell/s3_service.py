"""Async S3 operations for file storage.

Uses aioboto3 for non-blocking uploads and presigned URL generation.
Shared by Platform file upload and content storage handling
(compaction S3 expansion).
"""

import io
import time
from typing import BinaryIO

import aioboto3
from botocore.exceptions import ClientError

from common.config.settings import settings
from common.utils.logger import get_logger

logger = get_logger(__name__)


class S3Service:
    MAX_PRESIGNED_URL_CACHE_ENTRIES = 1024

    def __init__(self):
        self._session = aioboto3.Session(
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            region_name=settings.s3_region,
        )
        self._bucket = settings.s3_bucket_name
        self._region = settings.s3_region
        self._presigned_url_ttl = settings.s3_presigned_url_ttl
        self._url_cache: dict[tuple[str, str | None], tuple[str, float]] = {}
        self._cache_ttl = self._presigned_url_ttl / 2
        self._max_url_cache_entries = self.MAX_PRESIGNED_URL_CACHE_ENTRIES

    async def upload_file(
        self,
        file_data: BinaryIO | bytes,
        s3_key: str,
        content_type: str,
        content_length: int | None = None,
    ) -> str:
        """Upload file to S3. Returns the s3_key for storage in MongoDB."""
        extra_args: dict = {"ContentType": content_type}

        async with self._session.client("s3", region_name=self._region) as client:
            body: BinaryIO = io.BytesIO(file_data) if isinstance(file_data, bytes) else file_data
            await client.upload_fileobj(body, self._bucket, s3_key, ExtraArgs=extra_args)

        logger.info("Uploaded %s to S3 (%s bytes)", s3_key, content_length)
        return s3_key

    async def generate_presigned_url(
        self, s3_key: str, *, filename: str | None = None,
    ) -> str:
        """Generate a presigned GET URL with in-memory caching.

        If *filename* is provided, a ``Content-Disposition: attachment``
        header is baked into the URL so browsers download the file with
        the original name instead of the S3 key.
        """
        cache_key = self._cache_key(s3_key, filename)
        now = time.time()
        self._sweep_expired_presigned_urls(now)
        cached = self._url_cache.get(cache_key)
        if cached:
            url, expiry = cached
            if now < expiry:
                return url

        params: dict = {"Bucket": self._bucket, "Key": s3_key}
        if filename:
            from urllib.parse import quote

            safe_name = quote(filename, safe="")
            params["ResponseContentDisposition"] = (
                f"attachment; filename*=UTF-8''{safe_name}"
            )

        async with self._session.client("s3", region_name=self._region) as client:
            url = await client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=self._presigned_url_ttl,
            )

        self._cache_presigned_url(cache_key, url, now=time.time())
        return url

    async def batch_presigned_urls(
        self,
        s3_keys: list[str],
        *,
        filenames: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Generate presigned URLs for multiple keys. Uses cache where available.

        If *filenames* maps an s3_key to an original filename, the generated
        URL will include a ``Content-Disposition: attachment`` header so that
        browsers download with the correct name.
        """
        from urllib.parse import quote

        filenames = filenames or {}
        result: dict[str, str] = {}
        uncached: list[str] = []
        now = time.time()
        self._sweep_expired_presigned_urls(now)

        for key in s3_keys:
            fname = filenames.get(key)
            cache_key = self._cache_key(key, fname)
            cached = self._url_cache.get(cache_key)
            if cached:
                url, expiry = cached
                if now < expiry:
                    result[key] = url
                    continue
            uncached.append(key)

        if uncached:
            async with self._session.client("s3", region_name=self._region) as client:
                now = time.time()
                for key in uncached:
                    params: dict = {"Bucket": self._bucket, "Key": key}
                    fname = filenames.get(key)
                    if fname:
                        safe_name = quote(fname, safe="")
                        params["ResponseContentDisposition"] = (
                            f"attachment; filename*=UTF-8''{safe_name}"
                        )
                    url = await client.generate_presigned_url(
                        "get_object",
                        Params=params,
                        ExpiresIn=self._presigned_url_ttl,
                    )
                    result[key] = url
                    cache_key = self._cache_key(key, fname)
                    self._cache_presigned_url(cache_key, url, now=now)

        return result

    async def delete_file(self, s3_key: str) -> bool:
        """Delete a file from S3. Returns True if deleted."""
        try:
            async with self._session.client("s3", region_name=self._region) as client:
                await client.delete_object(Bucket=self._bucket, Key=s3_key)
            self._invalidate_presigned_url_cache(s3_key)
            return True
        except ClientError:
            logger.exception("Failed to delete S3 object: %s", s3_key)
            return False

    async def head_file(self, s3_key: str) -> dict | None:
        """Check if file exists and get metadata. Returns None if not found."""
        try:
            async with self._session.client("s3", region_name=self._region) as client:
                resp = await client.head_object(Bucket=self._bucket, Key=s3_key)
            return {
                "content_type": resp.get("ContentType"),
                "content_length": resp.get("ContentLength"),
                "last_modified": resp.get("LastModified"),
            }
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return None
            raise

    async def delete_prefix(self, prefix: str) -> int:
        """Delete all objects under an S3 prefix. Returns count deleted."""
        deleted = 0
        async with self._session.resource("s3", region_name=self._region) as s3:
            bucket = await s3.Bucket(self._bucket)
            async for obj in bucket.objects.filter(Prefix=prefix):
                await obj.delete()
                deleted += 1
        if deleted:
            logger.info("Deleted %d objects under prefix %s", deleted, prefix)
        return deleted

    def get_public_url(self, s3_key: str) -> str:
        """Return the permanent public URL for an S3 object.

        Requires the S3 bucket policy to grant public s3:GetObject on the
        relevant prefix (e.g. agent-avatars/*).
        """
        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{s3_key}"

    async def download_text(self, s3_key: str) -> str | None:
        """Download a text file from S3 and return its content as a string."""
        try:
            async with self._session.client("s3", region_name=self._region) as client:
                resp = await client.get_object(Bucket=self._bucket, Key=s3_key)
                body = await resp["Body"].read()
            return body.decode("utf-8")
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise

    async def get_text(self, s3_key: str) -> str | None:
        """ObjectStorageDAL-compatible alias for text reads."""
        return await self.download_text(s3_key)

    @staticmethod
    def _cache_key(s3_key: str, filename: str | None) -> tuple[str, str | None]:
        return (s3_key, filename or None)

    def _sweep_expired_presigned_urls(self, now: float) -> None:
        for cache_key, (_url, expiry) in list(self._url_cache.items()):
            if expiry <= now:
                self._url_cache.pop(cache_key, None)

    def _cache_presigned_url(
        self,
        cache_key: tuple[str, str | None],
        url: str,
        *,
        now: float,
    ) -> None:
        self._sweep_expired_presigned_urls(now)
        while (
            cache_key not in self._url_cache
            and len(self._url_cache) >= self._max_url_cache_entries
        ):
            oldest_key = min(self._url_cache, key=lambda key: self._url_cache[key][1])
            self._url_cache.pop(oldest_key, None)
        self._url_cache[cache_key] = (url, now + self._cache_ttl)

    def _invalidate_presigned_url_cache(self, s3_key: str) -> None:
        for cache_key in list(self._url_cache):
            if cache_key[0] == s3_key:
                self._url_cache.pop(cache_key, None)


s3_service = S3Service()
