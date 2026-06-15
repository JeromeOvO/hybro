"""Object-storage port protocol.

Defines the minimal surface that production code should depend on when it needs
S3-compatible object-storage operations (presigned URLs, prefix deletion, etc.).

The concrete implementation lives in ``app_shell.s3_service`` — this module
exists so that domain and orchestration layers can depend on the *protocol*
without importing infrastructure singletons directly.
"""

from __future__ import annotations

from typing import Protocol


class ObjectStoragePort(Protocol):
    """Protocol for object-storage operations used by room/execution layers."""

    async def upload_file(
        self,
        *,
        file_data: bytes,
        s3_key: str,
        content_type: str,
        content_length: int,
    ) -> None: ...

    async def generate_presigned_url(
        self,
        s3_key: str,
        *,
        filename: str | None = None,
        expires_in: int = 3600,
    ) -> str: ...

    async def batch_presigned_urls(
        self,
        s3_keys: list[str],
        *,
        filenames: dict[str, str] | None = None,
        expires_in: int = 3600,
    ) -> dict[str, str]: ...

    async def delete_prefix(self, prefix: str) -> None: ...

    def get_public_url(self, s3_key: str) -> str: ...
