from datetime import datetime
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from common.dto import FileInfo, RateLimitResult


@runtime_checkable
class APIKeyPrincipal(Protocol):
    key_id: str
    user_id: str
    name: str
    is_active: bool


@runtime_checkable
class APIKeyRecord(APIKeyPrincipal, Protocol):
    created_at: datetime
    last_used_at: datetime | None
    usage_count: int
    key_hash: str


@runtime_checkable
class APIKeyAuthenticator(Protocol):
    async def validate_api_key(
        self, api_key: str, *, track_usage: bool = True
    ) -> APIKeyPrincipal: ...


@runtime_checkable
class APIKeyStore(Protocol):
    async def get_api_keys_by_user(self, user_id: str) -> list[APIKeyRecord]: ...
    async def add_api_key(self, api_key: APIKeyRecord) -> object: ...
    async def get_api_key_by_id(self, key_id: str) -> APIKeyRecord | None: ...
    async def deactivate_api_key(self, key_id: str) -> bool: ...


@runtime_checkable
class GatewayDiscoveryProvider(Protocol):
    async def discover_agents(self, query: str, limit: int | None = None): ...


@runtime_checkable
class GatewayService(Protocol):
    async def discover_agents(
        self, query: str, limit: int | None, user_id: str
    ) -> Any: ...
    async def get_agent_card(self, agent_id: str, user_id: str) -> dict: ...
    async def send_message(
        self, agent_id: str, message: Any, user_id: str
    ) -> Any: ...
    async def prepare_stream(
        self, agent_id: str, message: Any, user_id: str
    ) -> AsyncIterator[dict]: ...
    async def stream_message(
        self, agent_id: str, message: Any, user_id: str
    ) -> AsyncIterator[dict]: ...


@runtime_checkable
class RateLimiter(Protocol):
    async def check(self, key: str, limit: int, window: int) -> RateLimitResult: ...
    async def check_global(self, limit: int, window: int) -> RateLimitResult: ...


@runtime_checkable
class APIKeyRateLimiter(RateLimiter, Protocol):
    async def record(self, key: str, **extra: Any) -> None: ...
    async def check_rate_limit(self, api_key: APIKeyPrincipal) -> None: ...
    async def record_request(self, api_key: APIKeyPrincipal) -> None: ...


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


__all__ = [
    "APIKeyAuthenticator",
    "APIKeyRateLimiter",
    "APIKeyPrincipal",
    "APIKeyRecord",
    "APIKeyStore",
    "FileStorage",
    "GatewayDiscoveryProvider",
    "GatewayService",
    "RateLimiter",
]
