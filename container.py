from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agent import AgentFacade, AgentMongoRepository
from common.protocols import (
    AgentCardResolver,
    AgentExclusionReader,
    AgentManagement,
    AgentMatcher,
    AgentRegistry,
    AgentRegistryWriter,
    ContentStorageRepository,
    ContextAssembler,
    EventPublisher,
    ExecutionEngine,
    HubLivenessReader,
    HITLManager,
    LLMProvider,
    MemoryManager,
    MemoryProjector,
    MongoCollection,
    MemoryRepository,
    MongoDAL,
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
from common.observability import MetricsCollector, traced_create_task
from common.utils.time import utcnow
from config.settings import settings
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
from room import MessageMongoRepository, RoomFacade, RoomMongoRepository


@dataclass(frozen=True)
class AgentDeps:
    agent_registry: AgentRegistry
    agent_matcher: AgentMatcher
    agent_management: AgentManagement
    agent_registry_writer: AgentRegistryWriter


@dataclass(frozen=True)
class RoomDeps:
    room_registry: RoomRegistry
    room_management: RoomManagement
    room_message_store: RoomMessageStore
    room_history_reader: RoomHistoryReader
    room_ownership_reader: RoomOwnershipReader


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


def create_mongo_dal(*, database: Any) -> MongoDAL:
    from dal.mongo import MongoDALImpl

    return MongoDALImpl(database=database)


def create_vector_dal() -> VectorDAL:
    from dal.pinecone import VectorDALImpl

    return VectorDALImpl()


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
    )


def create_agent_deps(
    *,
    mongo: MongoDAL,
    vector: VectorDAL,
    llm_provider: LLMProvider,
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
    )


def create_room_deps(
    *,
    mongo: MongoDAL,
    agent_registry: AgentRegistry,
    membership_source: RoomMembershipSeedSource,
) -> RoomDeps:
    repository = RoomMongoRepository(mongo=mongo)
    message_repository = MessageMongoRepository(mongo=mongo)
    facade = RoomFacade(
        repository=repository,
        message_repository=message_repository,
        agent_registry=agent_registry,
        membership_source=membership_source,
        id_factory=lambda: uuid4().hex,
        now=utcnow,
    )
    return RoomDeps(
        room_registry=facade,
        room_management=facade,
        room_message_store=facade,
        room_history_reader=facade,
        room_ownership_reader=facade,
    )


def create_context_memory_facade(
    *,
    mongo: MongoDAL,
    vector: VectorDAL,
    llm_provider: LLMProvider,
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
        concurrency=_legacy_compaction_concurrency(),
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


def _legacy_compaction_concurrency() -> int:
    try:
        return max(1, int(os.getenv("COMPACTION_CONCURRENCY", "5")))
    except (TypeError, ValueError):
        return 5
