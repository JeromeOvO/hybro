from collections.abc import AsyncIterator

from common.dto import FileInfo, GatewayRequest, GatewayResponse, RateLimitResult
from common.protocols import FileStorage, GatewayService, RateLimiter
from platform_module.config import PlatformConfig
from platform_module.deps import PlatformDeps
from platform_module.rate_limit import PlatformProtocolRateLimiter


class PlatformGatewayService:
    def __init__(self, config: PlatformConfig, deps: PlatformDeps) -> None:
        self._config = config
        self._deps = deps

    async def send_message(
        self, api_key: str, request: GatewayRequest
    ) -> GatewayResponse:
        raise NotImplementedError("Platform gateway send is not migrated yet")

    async def stream_message(
        self, api_key: str, request: GatewayRequest
    ) -> AsyncIterator[dict]:
        raise NotImplementedError("Platform gateway stream is not migrated yet")
        yield {}


class PlatformFileStorage:
    def __init__(self, config: PlatformConfig, deps: PlatformDeps) -> None:
        self._config = config
        self._deps = deps

    async def upload(
        self,
        file_bytes: bytes,
        filename: str,
        owner_id: str,
        room_id: str,
        **kwargs,
    ) -> FileInfo:
        raise NotImplementedError("Platform file upload is not migrated yet")

    async def get_url(self, file_id: str, ttl: int = 3600) -> str | None:
        raise NotImplementedError("Platform file URL lookup is not migrated yet")

    async def delete(self, file_id: str) -> bool:
        raise NotImplementedError("Platform file delete is not migrated yet")

    async def list_for_room(self, room_id: str) -> list[FileInfo]:
        raise NotImplementedError("Platform room file listing is not migrated yet")


class PlatformFacade:
    def __init__(self, config: PlatformConfig, deps: PlatformDeps) -> None:
        self.config = config
        self.deps = deps
        self._gateway_service = PlatformGatewayService(config, deps)
        self._gateway_rate_limiter = PlatformProtocolRateLimiter(
            deps.gateway_rate_limit_collection,
            scope="gateway",
            clock=deps.clock,
        )
        self._discovery_rate_limiter = PlatformProtocolRateLimiter(
            deps.discovery_rate_limit_collection,
            scope="discovery",
            clock=deps.clock,
        )
        self._agent_rate_limiter = PlatformProtocolRateLimiter(
            deps.agent_rate_limit_collection,
            scope="agent",
            clock=deps.clock,
        )
        self._file_storage = PlatformFileStorage(config, deps)

    @property
    def gateway_service(self) -> GatewayService:
        return self._gateway_service

    @property
    def gateway_rate_limiter(self) -> RateLimiter:
        return self._gateway_rate_limiter

    @property
    def discovery_rate_limiter(self) -> RateLimiter:
        return self._discovery_rate_limiter

    @property
    def agent_rate_limiter(self) -> RateLimiter:
        return self._agent_rate_limiter

    @property
    def file_storage(self) -> FileStorage:
        return self._file_storage


__all__ = ["PlatformFacade"]
