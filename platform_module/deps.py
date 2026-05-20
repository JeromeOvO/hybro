from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from common.protocols import (
    AgentCardResolver,
    AgentManagement,
    AgentMatcher,
    AgentRegistry,
    AgentTransport,
    ContentStorageRepository,
    GatewayDiscoveryProvider,
    ObjectStorageDAL,
    RedisKV,
)


class LoggerLike(Protocol):
    def info(self, message: str, *args, **kwargs) -> None: ...
    def warning(self, message: str, *args, **kwargs) -> None: ...
    def error(self, message: str, *args, **kwargs) -> None: ...


class DiscoveryQueryExpander(Protocol):
    async def expand_query_for_discovery(self, query: str) -> str: ...


class FileMetadataRepository(Protocol):
    async def create(self, data: dict) -> str: ...
    async def get(self, file_id: str) -> dict | None: ...
    async def delete(self, file_id: str) -> bool: ...
    async def list_for_room(self, room_id: str) -> list[dict]: ...


class RateLimitCollection(Protocol):
    async def count_documents(self, query: dict) -> int: ...
    async def find_one(
        self, query: dict, sort: list[tuple[str, int]] | None = None
    ) -> dict | None: ...
    async def insert_one(self, doc: dict): ...


@dataclass(frozen=True)
class PlatformDeps:
    agent_registry: AgentRegistry | None = None
    agent_matcher: AgentMatcher | None = None
    agent_management: AgentManagement | None = None
    discovery_provider: GatewayDiscoveryProvider | None = None
    discovery_query_expander: DiscoveryQueryExpander | None = None
    agent_transport: AgentTransport | None = None
    agent_card_resolver: AgentCardResolver | None = None
    redis: RedisKV | None = None
    gateway_rate_limit_collection: RateLimitCollection | None = None
    discovery_rate_limit_collection: RateLimitCollection | None = None
    agent_rate_limit_collection: RateLimitCollection | None = None
    object_storage: ObjectStorageDAL | None = None
    file_metadata_repository: FileMetadataRepository | None = None
    content_storage_repository: ContentStorageRepository | None = None
    file_id_factory: Callable[[], str] = lambda: uuid4().hex
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    logger: LoggerLike | None = None


__all__ = [
    "FileMetadataRepository",
    "DiscoveryQueryExpander",
    "LoggerLike",
    "PlatformDeps",
    "RateLimitCollection",
]
