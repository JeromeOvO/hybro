from common.protocols import (
    APIKeyRateLimiter,
    FileStorage,
    GatewayDiscoveryProvider,
    GatewayService,
    RateLimiter,
)
from platform_module.config import PlatformConfig
from platform_module.content_storage import PlatformContentStorage
from platform_module.deps import PlatformDeps
from platform_module.discovery import PlatformDiscovery
from platform_module.files import PlatformFileStorage
from platform_module.gateway import PlatformGateway
from platform_module.rate_limit import (
    PlatformProtocolRateLimiter,
    PlatformRouteAPIKeyRateLimiter,
)


class PlatformFacade:
    def __init__(self, config: PlatformConfig, deps: PlatformDeps) -> None:
        self.config = config
        self.deps = deps
        self._gateway_service = PlatformGateway(config, deps)
        self._discovery_service = PlatformDiscovery(config, deps)
        self._gateway_rate_limiter = PlatformRouteAPIKeyRateLimiter(
            deps.gateway_rate_limit_collection,
            scope="gateway",
            clock=deps.clock,
            per_key_limit=config.gateway_rate_limit_per_key,
            global_limit=config.gateway_rate_limit_global,
            per_key_limit_message=lambda limit: (
                f"Rate limit exceeded: {limit} requests per hour"
            ),
            global_limit_message="Service temporarily unavailable due to high traffic",
        )
        self._discovery_rate_limiter = PlatformRouteAPIKeyRateLimiter(
            deps.discovery_rate_limit_collection,
            scope="discovery",
            clock=deps.clock,
            per_key_limit=config.discovery_rate_limit_per_key,
            global_limit=config.discovery_rate_limit_global,
            per_key_limit_message=lambda limit: (
                f"Rate limit exceeded: {limit} requests per hour"
            ),
            global_limit_message="Service temporarily unavailable due to high traffic",
        )
        self._agent_rate_limiter = PlatformProtocolRateLimiter(
            deps.agent_rate_limit_collection,
            scope="agent",
            clock=deps.clock,
        )
        self._file_storage = PlatformFileStorage(config, deps)
        self._content_storage = PlatformContentStorage(config, deps)

    @property
    def gateway_service(self) -> GatewayService:
        return self._gateway_service

    @property
    def discovery_service(self) -> GatewayDiscoveryProvider:
        return self._discovery_service

    @property
    def gateway_rate_limiter(self) -> APIKeyRateLimiter:
        return self._gateway_rate_limiter

    @property
    def discovery_rate_limiter(self) -> APIKeyRateLimiter:
        return self._discovery_rate_limiter

    @property
    def agent_rate_limiter(self) -> RateLimiter:
        return self._agent_rate_limiter

    @property
    def file_storage(self) -> FileStorage:
        return self._file_storage

    @property
    def content_storage(self) -> PlatformContentStorage:
        return self._content_storage


__all__ = ["PlatformFacade"]
