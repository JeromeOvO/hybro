"""SDK-free platform object-storage adapter.

Defines the runtime compatibility surface used by room, execution, artifact,
and cleanup code while delegating SDK-owned behavior to the DAL layer.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import BinaryIO, Protocol

from common.errors import ObjectStorageError
from common.protocols import ObjectStorageDAL


class ObjectStoragePort(Protocol):
    """Protocol for object-storage operations used by room/execution layers."""

    async def upload_file(
        self,
        file_data: BinaryIO | bytes,
        s3_key: str,
        content_type: str,
        content_length: int | None = None,
    ) -> str: ...

    async def generate_presigned_url(
        self,
        s3_key: str,
        *,
        filename: str | None = None,
        expires_in: int | None = None,
    ) -> str: ...

    async def batch_presigned_urls(
        self,
        s3_keys: list[str],
        *,
        filenames: dict[str, str] | None = None,
        expires_in: int | None = None,
    ) -> dict[str, str]: ...

    async def delete_file(self, s3_key: str) -> bool: ...
    async def head_file(self, s3_key: str) -> dict | None: ...
    async def delete_prefix(self, prefix: str) -> int: ...

    def get_public_url(self, s3_key: str) -> str: ...

    async def download_text(self, s3_key: str) -> str | None: ...


class PlatformObjectStorage:
    """Compatibility-shaped object-storage adapter over ``ObjectStorageDAL``."""

    MAX_PRESIGNED_URL_CACHE_ENTRIES = 1024

    def __init__(
        self,
        dal: ObjectStorageDAL,
        *,
        default_presigned_url_ttl: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._dal = dal
        self._default_presigned_url_ttl = default_presigned_url_ttl
        self._clock = clock
        self._presigned_cache: dict[tuple[str, str | None, int], tuple[float, str]] = {}
        self._max_presigned_cache_entries = self.MAX_PRESIGNED_URL_CACHE_ENTRIES

    async def upload_file(
        self,
        file_data: BinaryIO | bytes,
        s3_key: str,
        content_type: str,
        content_length: int | None = None,
    ) -> str:
        if isinstance(file_data, bytes):
            result = await self._dal.put(s3_key, file_data, content_type)
        else:
            result = await self._dal.put_file(
                s3_key,
                file_data,
                content_type=content_type,
                content_length=content_length,
            )
        self._invalidate_presigned_cache(s3_key)
        return result

    async def generate_presigned_url(
        self,
        s3_key: str,
        *,
        filename: str | None = None,
        expires_in: int | None = None,
    ) -> str:
        ttl = self._effective_ttl(expires_in)
        cache_key = self._cache_key(s3_key, filename, ttl)
        now = self._clock()
        self._sweep_expired_presigned_cache(now)
        cached = self._presigned_cache.get(cache_key)
        if cached is not None:
            expires_at, url = cached
            if expires_at > now:
                return url

        url = await self._dal.get_presigned_url(s3_key, ttl=ttl, filename=filename)
        self._cache_presigned_url(
            cache_key,
            url,
            now=now,
            expires_at=now + max(ttl / 2, 0),
        )
        return url

    async def batch_presigned_urls(
        self,
        s3_keys: list[str],
        *,
        filenames: dict[str, str] | None = None,
        expires_in: int | None = None,
    ) -> dict[str, str]:
        return {
            s3_key: await self.generate_presigned_url(
                s3_key,
                filename=(filenames or {}).get(s3_key),
                expires_in=expires_in,
            )
            for s3_key in s3_keys
        }

    async def delete_file(self, s3_key: str) -> bool:
        try:
            deleted = await self._dal.delete(s3_key)
        except ObjectStorageError:
            return False
        if deleted:
            self._invalidate_presigned_cache(s3_key)
        return deleted

    async def head_file(self, s3_key: str) -> dict | None:
        return await self._dal.head(s3_key)

    async def delete_prefix(self, prefix: str) -> int:
        return await self._dal.delete_prefix(prefix)

    def get_public_url(self, s3_key: str) -> str:
        return self._dal.get_public_url(s3_key)

    async def download_text(self, s3_key: str) -> str | None:
        return await self._dal.get_text(s3_key)

    def _effective_ttl(self, expires_in: int | None) -> int:
        return self._default_presigned_url_ttl if expires_in is None else expires_in

    @staticmethod
    def _cache_key(
        s3_key: str,
        filename: str | None,
        ttl: int,
    ) -> tuple[str, str | None, int]:
        return (s3_key, filename, ttl)

    def _sweep_expired_presigned_cache(self, now: float) -> None:
        for cache_key, (expires_at, _url) in list(self._presigned_cache.items()):
            if expires_at <= now:
                self._presigned_cache.pop(cache_key, None)

    def _cache_presigned_url(
        self,
        cache_key: tuple[str, str | None, int],
        url: str,
        *,
        now: float,
        expires_at: float,
    ) -> None:
        if self._max_presigned_cache_entries <= 0:
            return
        self._sweep_expired_presigned_cache(now)
        while (
            cache_key not in self._presigned_cache
            and len(self._presigned_cache) >= self._max_presigned_cache_entries
        ):
            oldest_key = min(
                self._presigned_cache,
                key=lambda key: self._presigned_cache[key][0],
            )
            self._presigned_cache.pop(oldest_key, None)
        self._presigned_cache[cache_key] = (expires_at, url)

    def _invalidate_presigned_cache(self, s3_key: str) -> None:
        for cache_key in list(self._presigned_cache):
            if cache_key[0] == s3_key:
                self._presigned_cache.pop(cache_key, None)


__all__ = ["ObjectStoragePort", "PlatformObjectStorage"]
