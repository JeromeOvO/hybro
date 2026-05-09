from typing import Protocol, runtime_checkable

from common.dto import (
    FileInfo,
    FileMetadata,
    GatewayRequest,
    GatewayResponse,
    GatewayRoute,
    RateLimitResult,
)


@runtime_checkable
class GatewayService(Protocol):
    async def send_message(self, request: GatewayRequest) -> GatewayResponse: ...
    async def prepare_stream(self, agent_id: str, room_id: str) -> GatewayRoute: ...


@runtime_checkable
class RateLimiter(Protocol):
    async def check_rate_limit(self, key: str, scope: str | None = None) -> RateLimitResult: ...
    async def record_request(self, key: str, scope: str | None = None) -> None: ...


@runtime_checkable
class FileStorage(Protocol):
    async def upload(
        self,
        room_id: str,
        user_id: str,
        file_name: str,
        content: bytes,
        mime_type: str,
    ) -> FileMetadata: ...
    async def get_file(self, file_id: str) -> FileInfo | None: ...


__all__ = [
    "FileStorage",
    "GatewayService",
    "RateLimiter",
]
