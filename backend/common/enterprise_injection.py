from typing import Any, Protocol


class PlatformFacadeProtocol(Protocol):
    @property
    def gateway_service(self) -> Any: ...
    @property
    def discovery_service(self) -> Any: ...
    @property
    def gateway_rate_limiter(self) -> Any: ...
    @property
    def discovery_rate_limiter(self) -> Any: ...
    @property
    def file_storage(self) -> Any: ...
    @property
    def content_storage(self) -> Any: ...


class AgentRateLimiterProtocol(Protocol):
    async def check_limit(self, agent_id: str, *args, **kwargs) -> None: ...


class NoOpAgentRateLimiter:
    async def check_limit(self, agent_id: str, *args, **kwargs) -> None:
        pass


class AttachmentMetadataReaderProtocol(Protocol):
    async def get_metadata(self, attachment_id: str) -> dict: ...

class NoOpAttachmentMetadataReader:
    async def get_metadata(self, attachment_id: str) -> dict:
        return {}


class AttachmentCleanupPortProtocol(Protocol):
    async def cleanup(self, attachment_id: str) -> None: ...

class NoOpAttachmentCleanupPort:
    async def cleanup(self, attachment_id: str) -> None:
        pass
