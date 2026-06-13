from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agent import AgentFacade, AgentMongoRepository
from common.config.settings import settings
from common.dto import VectorRecord
from common.observability import MetricsCollector, traced_create_task
from common.protocols import (
    AgentCallCounter,
    AgentCardResolver,
    AgentExclusionReader,
    AgentManagement,
    AgentMatcher,
    AgentRegistry,
    AgentRegistryWriter,
    AgentRepository,
    AgentTransport,
    ContentStorageRepository,
    ContextAssembler,
    EventPublisher,
    ExecutionEngine,
    GatewayDiscoveryProvider,
    HITLManager,
    HubAgentResponseSink,
    HubDispatchPolicy,
    HubDispatchPort,
    HubLivenessReader,
    HubManagement,
    LLMEmbeddingGateway,
    LLMGateway,
    MemoryManager,
    MemoryProjector,
    MemoryRepository,
    MongoCollection,
    MongoDAL,
    ObjectStorageDAL,
    RedisKV,
    RedisPubSub,
    RoomHistoryReader,
    RoomManagement,
    RoomMembershipSeedSource,
    RoomMessageStore,
    RoomOwnershipReader,
    RoomRegistry,
    SSETransport,
    VectorDAL,
)
from common.utils.time import utcnow
from context_memory import (
    ContentStorageMongoRepository,
    ContextMemoryFacade,
    MemoryMongoRepository,
)
from context_memory.config import (
    CompactionConfig,
    ContextMemoryLLMConfig,
    MemorySearchConfig,
    TokenBudgetConfig,
)
from delivery.config import DeliveryConfig, DeliveryStartupPolicy
from delivery.event_bus import CrossInstanceEventBus
from delivery.event_publisher import EventPublisherImpl
from delivery.facade import DeliveryFacade
from delivery.sse.cancellation_watcher import CancellationWatcher
from delivery.sse.deduplication import TerminalStatusDeduplicator
from delivery.sse.manager import SSETransportImpl
from delivery.types import TaskRunner
from platform_module import PlatformConfig, PlatformDeps, PlatformFacade
from platform_module.adapters import (
    MongoFileMetadataRepository,
    RateLimitCollectionAdapter,
)
from platform_module.deps import DiscoveryQueryExpander, LoggerLike
from room import MessageMongoRepository, RoomFacade, RoomMongoRepository
from room.repository import RoomQuoteMongoRepository

if TYPE_CHECKING:
    from app_shell.repository_store import AppShellRepositoryStore


def create_execution_repositories(*, mongo: MongoDAL):
    from execution.repository.mongo import RunEventMongoRepository, RunMongoRepository

    return {
        "run_repository": RunMongoRepository(mongo),
        "run_event_repository": RunEventMongoRepository(mongo),
    }


@dataclass(frozen=True)
class AgentDeps:
    agent_registry: AgentRegistry
    agent_matcher: AgentMatcher
    agent_management: AgentManagement
    agent_registry_writer: AgentRegistryWriter
    agent_call_counter: AgentCallCounter
    agent_repository: AgentRepository


@dataclass(frozen=True)
class RoomDeps:
    room_registry: RoomRegistry
    room_management: RoomManagement
    room_message_store: RoomMessageStore
    room_history_reader: RoomHistoryReader
    room_ownership_reader: RoomOwnershipReader
    room_repository: Any
    message_repository: Any
    room_quote_repository: Any | None = None


@dataclass(frozen=True)
class ContextMemoryDeps:
    context_assembler: ContextAssembler
    memory_manager: MemoryManager
    memory_projector: MemoryProjector


@dataclass(frozen=True)
class DeliveryDeps:
    event_publisher: EventPublisher
    sse_transport: SSETransport


@dataclass(frozen=True)
class ExecutionDeps:
    execution_engine: ExecutionEngine
    hitl_manager: HITLManager
    hub_agent_response_sink: HubAgentResponseSink


@dataclass(frozen=True)
class HubDeps:
    hub_management: HubManagement
    hub_liveness: HubLivenessReader
    hub_dispatch_port: HubDispatchPort
    hub_dispatch_policy: HubDispatchPolicy
    hub_facade: Any


def create_mongo_dal() -> MongoDAL:
    from dal.mongo import MongoDALImpl

    return MongoDALImpl()


def create_vector_dal() -> VectorDAL:
    from dal.pinecone import VectorDALImpl

    return VectorDALImpl()


async def ensure_runtime_indexes(*, mongo: MongoDAL) -> None:
    await _ensure_agent_indexes(mongo)
    await _ensure_context_memory_indexes(mongo)
    await _ensure_capability_issue_indexes(mongo)
    await _ensure_run_lifecycle_indexes(mongo)
    await _ensure_room_quote_indexes(mongo)
    await _ensure_task_tracking_indexes(mongo)


async def _ensure_agent_indexes(mongo: MongoDAL) -> None:
    agents = mongo.collection("agents")
    existing = await agents.index_information()
    index = existing.get("unique_normalized_url")
    needs_recreate = index is None or index.get("partialFilterExpression") != {
        "normalized_url": {"$type": "string"}
    }
    if not needs_recreate:
        return
    try:
        await agents.drop_index("unique_normalized_url")
    except Exception:
        pass
    await agents.create_index(
        [("normalized_url", 1)],
        unique=True,
        name="unique_normalized_url",
        partialFilterExpression={"normalized_url": {"$type": "string"}},
    )


async def _create_index(
    mongo: MongoDAL,
    collection_name: str,
    keys,
    *,
    name: str,
    unique: bool = False,
    critical: bool = False,
    **kwargs,
) -> None:
    collection = mongo.collection(collection_name)
    try:
        await collection.create_index(keys, unique=unique, name=name, **kwargs)
    except Exception as exc:
        logger = logging.getLogger(__name__)
        if unique and critical:
            logger.error(
                "Critical index creation failed for %s.%s",
                collection_name,
                name,
                exc_info=True,
            )
            raise RuntimeError(
                f"Critical index creation failed for {collection_name}.{name}"
            ) from exc
        logger.warning(
            "Index creation failed for %s.%s",
            collection_name,
            name,
            exc_info=True,
        )


async def _ensure_context_memory_indexes(mongo: MongoDAL) -> None:
    await _create_index(
        mongo,
        "conversation_content",
        [("room_id", 1), ("turn_id", 1)],
        name="room_turn_unique",
        unique=True,
        critical=True,
    )
    await _create_index(
        mongo,
        "conversation_content",
        [("document_id", 1)],
        name="document_id_unique",
        unique=True,
        partialFilterExpression={"document_id": {"$exists": True}},
    )
    await _create_index(
        mongo,
        "conversation_content",
        [("room_id", 1), ("stored_at", -1)],
        name="room_stored_at",
    )
    await _create_index(
        mongo,
        "conversation_content",
        [
            ("content", "text"),
            ("turn_notes.keywords", "text"),
            ("turn_notes.entities", "text"),
            ("turn_notes.one_liner", "text"),
        ],
        name="turn_notes_text",
    )
    await _create_index(
        mongo,
        "conversation_content",
        [("expires_at", 1)],
        name="content_ttl",
        expireAfterSeconds=0,
        sparse=True,
    )
    await _create_index(
        mongo,
        "user_memories",
        [("user_id", 1)],
        name="user_id_unique",
        unique=True,
        critical=True,
    )
    await _create_index(
        mongo,
        "agent_memories",
        [("agent_id", 1)],
        name="agent_id_unique",
        unique=True,
        critical=True,
    )
    await _create_index(
        mongo,
        "room_memories",
        [("room_id", 1)],
        name="room_id_unique",
        unique=True,
        critical=True,
    )


async def _ensure_capability_issue_indexes(mongo: MongoDAL) -> None:
    await _create_index(
        mongo,
        "agent_capability_issues",
        [("agent_id", 1), ("status", 1)],
        name="agent_id_status",
    )
    await _create_index(
        mongo,
        "agent_capability_issues",
        [("status", 1), ("agent_id", 1)],
        name="status_agent_id",
    )
    await _create_index(
        mongo,
        "agent_capability_issues",
        [("created_at", 1)],
        name="created_at",
    )
    await _create_index(
        mongo,
        "agent_capability_issues",
        [("issue_id", 1)],
        name="issue_id_unique",
        unique=True,
    )


async def _ensure_run_lifecycle_indexes(mongo: MongoDAL) -> None:
    await _create_index(
        mongo,
        "runs",
        [("run_id", 1)],
        name="run_id_unique",
        unique=True,
    )
    await _create_index(
        mongo,
        "runs",
        [("room_id", 1), ("state", 1), ("updated_at", -1)],
        name="room_state_updated_at",
    )
    await _create_index(
        mongo,
        "runs",
        [("room_id", 1), ("client_request_id", 1), ("agent_id", 1)],
        name="room_client_agent_idempotency",
        unique=True,
        partialFilterExpression={
            "client_request_id": {"$type": "string"},
            "agent_id": {"$type": "string"},
        },
    )
    await _create_index(
        mongo,
        "run_events",
        [("event_id", 1)],
        name="event_id_unique",
        unique=True,
    )
    await _create_index(
        mongo,
        "run_events",
        [("run_id", 1), ("seq", 1)],
        name="run_seq_unique",
        unique=True,
    )
    await _create_index(
        mongo,
        "run_events",
        [("room_id", 1), ("ts", -1)],
        name="room_ts",
    )


async def _ensure_room_quote_indexes(mongo: MongoDAL) -> None:
    await _create_index(
        mongo,
        "room_quotes",
        [("quote_id", 1)],
        name="quote_id_unique",
        unique=True,
    )
    await _create_index(
        mongo,
        "room_quotes",
        [("room_id", 1)],
        name="room_id_lookup",
    )


async def _ensure_task_tracking_indexes(mongo: MongoDAL) -> None:
    await _create_index(
        mongo,
        "room_agent_messages",
        [("has_task_tracking", 1)],
        name="has_task_tracking_sparse",
        sparse=True,
    )
    await _create_index(
        mongo,
        "room_agent_messages",
        [
            ("task_updated_at", 1),
            ("message_content.message_task.status.state", 1),
        ],
        name="task_updated_state_sparse",
        sparse=True,
    )
    await _create_index(
        mongo,
        "room_agent_messages",
        [
            ("task_created_at", 1),
            ("message_content.message_task.status.state", 1),
        ],
        name="task_created_state_sparse",
        sparse=True,
    )
    await _create_index(
        mongo,
        "room_agent_messages",
        [
            ("user_id", 1),
            ("message_content.message_task.status.state", 1),
            ("has_task_tracking", 1),
        ],
        name="user_task_state_sparse",
        sparse=True,
    )
    await _create_index(
        mongo,
        "room_agent_messages",
        [("room_id", 1), ("has_task_tracking", 1), ("task_created_at", -1)],
        name="room_task_created_sparse",
        sparse=True,
    )


class AgentViewsetVectorIndexAdapter:
    def __init__(
        self,
        *,
        vector_dal: VectorDAL,
        index: str,
    ) -> None:
        self._vector_dal = vector_dal
        self._index = index

    def upsert(self, vectors: list[dict]) -> None:
        records = [
            VectorRecord(
                id=item["id"],
                vector=item.get("values", []),
                metadata=item.get("metadata", {}),
            )
            for item in vectors
        ]
        self._dispatch(self._vector_dal.upsert(self._index, records))

    def delete(self, ids: list[str]) -> None:
        self._dispatch(self._vector_dal.delete(self._index, ids))

    def _dispatch(self, operation: Awaitable[None]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(operation)
        else:
            loop.create_task(operation).add_done_callback(self._handle_task_error)

    @staticmethod
    def _handle_task_error(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        logging.getLogger(__name__).error(
            "Vector index update failed",
            exc_info=(exc.__class__, exc, exc.__traceback__),
        )


def create_agent_viewset_vector_index(*, vector: VectorDAL) -> AgentViewsetVectorIndexAdapter:
    index_name = settings.pinecone_index_name
    return AgentViewsetVectorIndexAdapter(vector_dal=vector, index=index_name)


def create_agent_capability_issue_repository(mongo: MongoDAL) -> Any:
    from agent.repository.capability_issue_mongo import (
        AgentCapabilityIssueMongoRepository,
    )

    return AgentCapabilityIssueMongoRepository(mongo=mongo)


def create_agent_capability_issue_service(*, repository: Any) -> Any:
    from agent.capability_issue import AgentCapabilityIssueService

    return AgentCapabilityIssueService(repository=repository)


def create_agent_resolver_repository(*, service) -> Any:
    from models.request import AgentCenterRequest

    class _ResolverRepository:
        async def query_similar_agents(
            self,
            query_text: str,
            count: int,
            allowed_agent_ids: list[str] | None,
            excluded_agent_ids: set[str],
            active_only: bool,
            user_id: str | None = None,
        ) -> list[Any]:
            del active_only  # compatibility with repository protocol
            request = AgentCenterRequest(
                query_text=query_text,
                user_id=user_id,
                agent_count=count,
            )
            response = await service.query_similar_agents(request)
            if response.success is False:
                return []
            candidates = response.agents or []
            if allowed_agent_ids is not None:
                allowed = set(allowed_agent_ids)
                candidates = [agent for agent in candidates if agent.agent_id in allowed]
            if excluded_agent_ids:
                candidates = [
                    agent
                    for agent in candidates
                    if agent.agent_id not in excluded_agent_ids
                ]
            return candidates

        async def get_agents_with_conditions_visible(
            self,
            user_id: str | None,
            query: dict,
            limit: int = 0,
        ) -> list[Any]:
            request = AgentCenterRequest(
                user_id=user_id,
                query=query,
                limit=limit,
            )
            response = await service.get_agents_with_conditions(request)
            if response.success is False:
                return []
            return response.agents or []

    return _ResolverRepository()


def create_object_storage_dal() -> ObjectStorageDAL:
    from dal.s3 import ObjectStorageDALImpl

    return ObjectStorageDALImpl()


def create_platform_config(app_settings: Any = settings) -> PlatformConfig:
    from models.file_upload import ALLOWED_MIME_TYPES

    return PlatformConfig(
        gateway_base_url=getattr(app_settings, "gateway_base_url", ""),
        api_prefix=getattr(app_settings, "api_prefix", "/api/v1"),
        gateway_rate_limit_per_key=getattr(
            app_settings, "gateway_rate_limit_per_key", 100
        ),
        gateway_rate_limit_global=getattr(
            app_settings, "gateway_rate_limit_global", 1000
        ),
        discovery_rate_limit_per_key=getattr(
            app_settings, "discovery_rate_limit_per_key", 100
        ),
        discovery_rate_limit_global=getattr(
            app_settings, "discovery_rate_limit_global", 1000
        ),
        discovery_default_limit=getattr(app_settings, "discovery_default_limit", 5),
        discovery_confidence_threshold=getattr(
            app_settings, "discovery_confidence_threshold", 0.0
        ),
        max_upload_size_bytes=getattr(app_settings, "max_file_size_mb", 25)
        * 1024
        * 1024,
        allowed_mime_types=tuple(sorted(ALLOWED_MIME_TYPES)),
        presigned_url_ttl_seconds=getattr(
            app_settings, "s3_presigned_url_ttl", 3600
        ),
        content_storage_ttl_seconds=getattr(
            app_settings, "compaction_content_ttl_days", 0
        )
        * 24
        * 60
        * 60,
    )


def create_platform_deps(
    *,
    agent_deps: AgentDeps,
    mongo: MongoDAL,
    agent_transport: AgentTransport,
    agent_card_resolver: AgentCardResolver | None = None,
    object_storage: ObjectStorageDAL | None = None,
    content_storage_repository: ContentStorageRepository | None = None,
    discovery_provider: GatewayDiscoveryProvider | None = None,
    discovery_query_expander: DiscoveryQueryExpander | None = None,
    redis: RedisKV | None = None,
    logger: LoggerLike | None = None,
) -> PlatformDeps:
    return PlatformDeps(
        agent_registry=agent_deps.agent_registry,
        agent_matcher=agent_deps.agent_matcher,
        agent_management=agent_deps.agent_management,
        discovery_provider=discovery_provider,
        discovery_query_expander=discovery_query_expander,
        agent_transport=agent_transport,
        agent_card_resolver=agent_card_resolver,
        agent_call_counter=agent_deps.agent_call_counter,
        redis=redis,
        gateway_rate_limit_collection=RateLimitCollectionAdapter(
            mongo.collection("gateway_api_requests"),
            "gateway_api_requests",
        ),
        discovery_rate_limit_collection=RateLimitCollectionAdapter(
            mongo.collection("discovery_api_requests"),
            "discovery_api_requests",
        ),
        agent_rate_limit_collection=RateLimitCollectionAdapter(
            mongo.collection("agent_requests"),
            "agent_requests",
        ),
        object_storage=object_storage,
        file_metadata_repository=MongoFileMetadataRepository(
            mongo.collection("file_uploads")
        ),
        content_storage_repository=content_storage_repository,
        clock=utcnow,
        logger=logger,
    )


def create_platform_facade(
    *, config: PlatformConfig, deps: PlatformDeps
) -> PlatformFacade:
    return PlatformFacade(config=config, deps=deps)


def create_delivery_config(app_settings: Any = settings) -> DeliveryConfig:
    values = {
        field: getattr(app_settings, field)
        for field in DeliveryConfig.__dataclass_fields__
    }
    terminal_statuses = values["terminal_processing_statuses"]
    if isinstance(terminal_statuses, str):
        values["terminal_processing_statuses"] = [
            status.strip()
            for status in terminal_statuses.split(",")
            if status.strip()
        ]
    return DeliveryConfig(**values)


def create_delivery_startup_policy(
    *,
    redis_url: str,
    multi_worker: bool,
) -> DeliveryStartupPolicy:
    redis_expected = bool(redis_url)
    return DeliveryStartupPolicy(
        redis_expected=redis_expected,
        multi_worker=multi_worker,
        allow_degraded_change_stream=not redis_expected and not multi_worker,
    )


def create_delivery_redis_clients(
    *,
    redis_url: str,
    config: DeliveryConfig,
) -> tuple[RedisKV | None, RedisPubSub | None]:
    if not redis_url:
        return None, None

    from dal.redis.kv import RedisKVImpl
    from dal.redis.pubsub import RedisPubSubImpl

    return (
        RedisKVImpl(url=redis_url),
        RedisPubSubImpl(
            url=redis_url,
            max_connections=config.redis_max_connections,
        ),
    )


def create_delivery_cancellation_collection(*, mongo: MongoDAL) -> MongoCollection:
    return mongo.collection("cancelled_messages")


def create_delivery_facade(
    *,
    cancellation_collection: MongoCollection,
    startup_policy: DeliveryStartupPolicy,
    redis_kv: RedisKV | None = None,
    redis_pubsub: RedisPubSub | None = None,
    config: DeliveryConfig | None = None,
    now: Callable[[], Any] | None = None,
    id_factory: Callable[[], str] | None = None,
    instance_id: str | None = None,
    task_runner: TaskRunner | None = None,
    metrics: MetricsCollector | None = None,
) -> DeliveryFacade:
    if cancellation_collection is None:
        raise ValueError("cancellation_collection is required")

    resolved_config = config or DeliveryConfig()
    resolved_now = now or utcnow
    resolved_id_factory = id_factory or (lambda: uuid4().hex)
    resolved_instance_id = instance_id or resolved_id_factory()
    resolved_task_runner = task_runner or traced_create_task

    event_bus = CrossInstanceEventBus(
        redis_pubsub=redis_pubsub,
        config=resolved_config,
        instance_id=resolved_instance_id,
        task_runner=resolved_task_runner,
        now=resolved_now,
    )
    cancellation_watcher = CancellationWatcher(
        collection=cancellation_collection,
        redis_kv=redis_kv,
        event_bus=event_bus,
        config=resolved_config,
        task_runner=resolved_task_runner,
    )
    sse_transport = SSETransportImpl(
        cancellation_watcher=cancellation_watcher,
        event_bus=event_bus,
        config=resolved_config,
        now=resolved_now,
        id_factory=resolved_id_factory,
        instance_id=resolved_instance_id,
        task_runner=resolved_task_runner,
        metrics=metrics,
    )
    deduplicator = TerminalStatusDeduplicator(
        redis_kv=redis_kv,
        config=resolved_config,
    )
    event_publisher = EventPublisherImpl(
        sse_transport=sse_transport,
        event_bus=event_bus,
        deduplicator=deduplicator,
        config=resolved_config,
        now=resolved_now,
        instance_id=resolved_instance_id,
        task_runner=resolved_task_runner,
        metrics=metrics,
    )
    event_bus.set_sse_callback(sse_transport.broadcast_frame_to_room)
    event_bus.set_cancellation_callback(cancellation_watcher.handle_remote_cancellation)
    event_bus.set_internal_callback(event_publisher.handle_remote_internal_event)

    return DeliveryFacade(
        event_publisher=event_publisher,
        sse_transport=sse_transport,
        event_bus=event_bus,
        cancellation_watcher=cancellation_watcher,
        redis_kv=redis_kv,
        config=resolved_config,
        startup_policy=startup_policy,
        instance_id=resolved_instance_id,
    )


def create_delivery_deps(facade: DeliveryFacade) -> DeliveryDeps:
    return DeliveryDeps(
        event_publisher=facade.event_publisher,
        sse_transport=facade.sse_transport,
    )


def create_execution_facade(**kwargs: Any):
    from execution.facade import ExecutionFacade

    return ExecutionFacade(**kwargs)


def create_execution_deps(facade) -> ExecutionDeps:
    return ExecutionDeps(
        execution_engine=facade,
        hitl_manager=facade,
        hub_agent_response_sink=facade,
    )


def create_hub_facade(**kwargs: Any):
    from hub_runtime_bridge import HubFacade

    return HubFacade(**kwargs)


def create_hub_deps(facade: Any) -> HubDeps:
    from hub_runtime_bridge.dispatch_adapter import HubDispatchAdapter
    from hub_runtime_bridge.service.dispatch_policy import (
        HubDispatchPolicy as HubPolicy,
    )

    return HubDeps(
        hub_management=facade,
        hub_liveness=facade,
        hub_dispatch_port=HubDispatchAdapter(
            facade,
            liveness_cache=getattr(facade, "_liveness_cache", None),
        ),
        hub_dispatch_policy=HubPolicy(facade),
        hub_facade=facade,
    )


def create_agent_deps(
    *,
    mongo: MongoDAL,
    vector: VectorDAL,
    llm_provider: LLMEmbeddingGateway,
    card_resolver: AgentCardResolver,
    hub_liveness: HubLivenessReader | None = None,
    exclusion_reader: AgentExclusionReader | None = None,
    gateway_base_url: str | None = None,
) -> AgentDeps:
    repository = AgentMongoRepository(mongo=mongo)
    facade = AgentFacade(
        repository=repository,
        vector=vector,
        llm_provider=llm_provider,
        card_resolver=card_resolver,
        agent_index=settings.pinecone_index_name,
        hub_liveness=hub_liveness,
        exclusion_reader=exclusion_reader,
        gateway_base_url=gateway_base_url,
        id_factory=lambda: uuid4().hex,
        now=utcnow,
    )
    return AgentDeps(
        agent_registry=facade,
        agent_matcher=facade,
        agent_management=facade,
        agent_registry_writer=facade,
        agent_call_counter=facade,
        agent_repository=repository,
    )


def create_room_deps(
    *,
    mongo: MongoDAL,
    agent_registry: AgentRegistry,
    membership_source: RoomMembershipSeedSource,
) -> RoomDeps:
    repository = RoomMongoRepository(mongo=mongo)
    message_repository = MessageMongoRepository(mongo=mongo)
    quote_repository = RoomQuoteMongoRepository(mongo=mongo)
    facade = RoomFacade(
        repository=repository,
        message_repository=message_repository,
        agent_registry=agent_registry,
        membership_source=membership_source,
        quote_repository=quote_repository,
        id_factory=lambda: uuid4().hex,
        now=utcnow,
    )
    return RoomDeps(
        room_registry=facade,
        room_management=facade,
        room_message_store=facade,
        room_history_reader=facade,
        room_ownership_reader=facade,
        room_repository=repository,
        message_repository=message_repository,
        room_quote_repository=quote_repository,
    )


def create_context_memory_facade(
    *,
    mongo: MongoDAL,
    vector: VectorDAL,
    llm_provider: LLMGateway,
    room_history_reader: RoomHistoryReader,
    memory_repository: MemoryRepository | None = None,
    content_repository: ContentStorageRepository | None = None,
    index_registry: Any | None = None,
    token_budget: TokenBudgetConfig | None = None,
    compaction_config: CompactionConfig | None = None,
    search_config: MemorySearchConfig | None = None,
    llm_config: ContextMemoryLLMConfig | None = None,
    background_task_runner: Callable[[Awaitable[Any]], None] | None = None,
) -> ContextMemoryFacade:
    memory_repository = memory_repository or MemoryMongoRepository(mongo=mongo)
    content_repository = content_repository or ContentStorageMongoRepository(
        mongo=mongo,
        index_registry=index_registry,
    )
    token_budget = token_budget or TokenBudgetConfig(
        model_context_window=settings.context_model_window,
        system_prompt=settings.context_system_prompt_tokens,
        tool_schemas=settings.context_tool_schema_tokens,
        response_reserve=settings.context_response_reserve_tokens,
        room_context_pct=settings.context_room_pct,
        conversation_history_pct=settings.context_history_pct,
        current_task_pct=settings.context_task_pct,
    )
    compaction_config = compaction_config or CompactionConfig(
        enabled=settings.compaction_enabled,
        max_full_turns=settings.compaction_max_full_turns,
        max_total_tokens=settings.compaction_max_total_tokens,
        preserve_recent_turns=settings.compaction_preserve_recent,
        content_ttl_days=settings.compaction_content_ttl_days,
        concurrency=settings.compaction_concurrency,
    )
    search_config = search_config or MemorySearchConfig(
        enabled=settings.memory_search_enabled,
        vector_weight=settings.memory_search_vector_weight,
        keyword_weight=settings.memory_search_keyword_weight,
        temporal_decay_enabled=settings.memory_search_temporal_decay_enabled,
        half_life_days=settings.memory_search_half_life_days,
        mmr_lambda=settings.memory_search_mmr_lambda,
        max_results=settings.memory_search_max_results,
        max_snippet_chars=settings.memory_search_max_snippet_chars,
        index_name=settings.memory_search_index_name,
    )
    return ContextMemoryFacade(
        memory_repository=memory_repository,
        content_repository=content_repository,
        room_history_reader=room_history_reader,
        vector=vector,
        llm_provider=llm_provider,
        id_factory=lambda: str(uuid4()),
        now=utcnow,
        token_budget=token_budget,
        compaction_config=compaction_config,
        search_config=search_config,
        llm_config=llm_config,
        background_task_runner=background_task_runner,
    )


def create_context_memory_deps(facade: ContextMemoryFacade) -> ContextMemoryDeps:
    return ContextMemoryDeps(
        context_assembler=facade,
        memory_manager=facade,
        memory_projector=facade,
    )


def create_api_key_store(*, mongo: MongoDAL):
    """Create Platform-owned API key store."""
    from platform_module.api_keys import MongoAPIKeyStore

    return MongoAPIKeyStore(mongo=mongo)


def create_app_shell_repository_store(
    *,
    mongo: MongoDAL,
    room_deps: RoomDeps,
    agent_deps: AgentDeps,
) -> AppShellRepositoryStore:
    from app_shell.repository_store import AppShellRepositoryStore

    return AppShellRepositoryStore(
        mongo=mongo,
        room_repository=room_deps.room_repository,
        message_repository=room_deps.message_repository,
        agent_repository=agent_deps.agent_repository,
    )
