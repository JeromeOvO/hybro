from common.protocols import FileStorage, GatewayService, RateLimiter
from platform_module.config import PlatformConfig
from platform_module.deps import PlatformDeps
from platform_module.files import PlatformFileStorage
from platform_module.gateway import PlatformGateway
from platform_module.rate_limit import PlatformProtocolRateLimiter


class PlatformFacade:
    def __init__(self, config: PlatformConfig, deps: PlatformDeps) -> None:
        self.config = config
        self.deps = deps
        self._gateway_service = PlatformGateway(config, deps)
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
