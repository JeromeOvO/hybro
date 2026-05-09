from typing import AsyncIterator, Protocol, runtime_checkable

from common.dto import FileInfo, GatewayRequest, GatewayResponse, RateLimitResult


@runtime_checkable
class GatewayService(Protocol):
    async def send_message(
        self, api_key: str, request: GatewayRequest
    ) -> GatewayResponse: ...
    async def stream_message(
        self, api_key: str, request: GatewayRequest
    ) -> AsyncIterator[dict]: ...


@runtime_checkable
class RateLimiter(Protocol):
    async def check(self, key: str, limit: int, window: int) -> RateLimitResult: ...
    async def check_global(self, limit: int, window: int) -> RateLimitResult: ...


@runtime_checkable
class FileStorage(Protocol):
    async def upload(
        self,
        file_bytes: bytes,
        filename: str,
        owner_id: str,
        room_id: str,
        **kwargs,
    ) -> FileInfo: ...
    async def get_url(self, file_id: str, ttl: int = 3600) -> str | None: ...
    async def delete(self, file_id: str) -> bool: ...
    async def list_for_room(self, room_id: str) -> list[FileInfo]: ...


__all__ = ["FileStorage", "GatewayService", "RateLimiter"]
