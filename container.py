from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from loguru import logger

from agent import AgentFacade, AgentMongoRepository
from api_gateway.dependencies import (
    APIGatewayDeps,
    bind_api_gateway_deps,
    missing_required_deps,
)
from api_gateway.routes import (
    a2a_task_routes as a2a_tasks,
)
from api_gateway.routes import (
    agent_group_routes as agent_group,
)
from api_gateway.routes import (
    agent_routes as agent,
)
from api_gateway.routes import (
    discovery_api_key_routes as discovery_api_keys,
)
from api_gateway.routes import (
    discovery_routes as discovery,
)
from api_gateway.routes import (
    files_routes as files,
)
from api_gateway.routes import (
    hitl_routes as hitl,
)
from api_gateway.routes import (
    hub_routes as hub,
)
from api_gateway.routes import (
    inspection_routes as inspection_center,
)
from api_gateway.routes import (
    memory_routes as memory_center,
)
from api_gateway.routes import (
    platform_gateway_routes as gateway,
)
from api_gateway.routes import (
    relay_routes as relay,
)
from api_gateway.routes import (
    room_routes as room_center,
)
from api_gateway.routes import (
    sse_routes as sse,
)
from api_gateway.routes import (
    webhook_routes as webhooks,
)
from api_gateway.viewsets import agent as agent_viewset
from api_gateway.viewsets import base as viewset
from app_shell.agent_health_service import agent_health_service
from app_shell.api_key_auth import MongoAPIKeyAuthenticator
from app_shell.delivery_runtime import sse_manager
from app_shell.viewset import AppShellDALViewSetRepositoryProvider
from common.api_key_auth import bind_api_key_authenticator
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
    ContextMemoryRuntime,
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
from jobs.cleanup_orphaned_uploads import (
    OrphanedUploadCleanerDeps,
    orphaned_upload_cleaner,
)
from jobs.compaction_sweep import CompactionSweepDeps, compaction_sweep
from jobs.constants import ALL_JOB_NAMES
from jobs.stale_task_checker import StaleTaskCheckerDeps, stale_task_checker
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


# Pure function — trivially testable without lifespan/DB
def check_multi_worker_safety(
    *,
    is_gunicorn: bool,
    delivery_pubsub_connected: bool,
    delivery_kv_connected: bool,
    redis_service_connected: bool,
    relay_streams_connected: bool,
    change_stream_connected: bool,
) -> None:
    """Refuse to start under gunicorn without fully connected Redis.

    Gunicorn workers are separate processes. Without Redis:
    - SSE broadcast is local-only (cross-worker delivery fails)
    - Background jobs run N times (no leader election)
    - Room locks use asyncio.Lock only (no cross-process coordination)
    - Relay uses in-memory queues (hub messages lost across workers)

    Raises:
        RuntimeError: if gunicorn detected and any Redis service is not connected
    """
    if not is_gunicorn:
        return

    problems = []
    if not delivery_pubsub_connected:
        problems.append("Delivery Pub/Sub not connected")
    if not delivery_kv_connected:
        problems.append("Delivery KV not connected")
    if not redis_service_connected:
        problems.append("RedisService (key-value) not connected")
    if not relay_streams_connected:
        problems.append("Relay streams not connected")
    if not change_stream_connected:
        problems.append("Cancellation change stream not connected")

    if problems:
        raise RuntimeError(
            "Running under gunicorn requires all Redis app_shell. "
            "Issues: " + "; ".join(problems) + ". "
            "Fix: set REDIS_URL to a running Redis instance, "
            "or use 'uvicorn main:app' for single-process mode."
        )
    logger.info("Multi-worker safety check passed: gunicorn + Redis OK")


@dataclass
class ApplicationRuntime:
    settings: Any
    _lifespan_context: Any | None = None


def create_application_runtime(app_settings: Any = settings) -> ApplicationRuntime:
    return ApplicationRuntime(settings=app_settings)


def create_health_check_service(
    *,
    redis_url: str,
    compute_health_status: Callable[..., dict[str, Any]],
) -> Any:
    from app_shell.health_check import AppShellHealthCheck

    return AppShellHealthCheck(
        redis_url=redis_url,
        compute_health_status=compute_health_status,
    )


async def startup_runtime(app: Any, runtime: ApplicationRuntime) -> None:
    if runtime._lifespan_context is not None:
        raise RuntimeError("Application runtime has already been started")
    context = _runtime_lifespan(app, runtime)
    runtime._lifespan_context = context
    try:
        await context.__aenter__()
    except BaseException:
        runtime._lifespan_context = None
        raise


async def shutdown_runtime(app: Any, runtime: ApplicationRuntime) -> None:
    del app
    context = runtime._lifespan_context
    if context is None:
        return
    try:
        await context.__aexit__(None, None, None)
    finally:
        runtime._lifespan_context = None


def validate_runtime_bindings(
    app: Any, runtime: ApplicationRuntime | None = None
) -> None:
    del runtime
    errors: list[str] = []

    if getattr(room_center, "execution_engine", None) is None:
        errors.append("api.room_center.execution_engine")

    from execution.orchestration.room_message_center import (
        room_message_center as execution_room_message_center,
    )

    if getattr(execution_room_message_center, "_runtime", None) is None:
        errors.append("execution.room_message_center")

    if getattr(sse_manager, "_facade", None) is None:
        errors.append("sse_manager.delivery_facade")

    from app_shell.hitl_service import hitl_service

    if getattr(hitl_service, "_service", None) is None:
        errors.append("hitl_service")

    if getattr(app.state, "execution_deps", None) is None:
        errors.append("app.state.execution_deps")

    if getattr(app.state, "platform_facade", None) is None:
        errors.append("app.state.platform_facade")
    for missing in missing_required_deps():
        errors.append(f"api_gateway.{missing}")

    if errors:
        raise RuntimeError(
            "Startup binding incomplete - missing: "
            + ", ".join(errors)
            + ". Cannot serve traffic."
        )

    logger.info("All startup bindings verified")


@asynccontextmanager
async def _runtime_lifespan(app: Any, runtime: ApplicationRuntime):  # noqa: C901
    """Lifespan context manager to handle startup and shutdown events.

    Startup is split into two phases with a multi-worker safety guard:
      Phase 1 — Infrastructure (DB + Redis, no background work)
      Guard   — Fail if gunicorn without fully connected Redis
      Phase 2 — Background services (only after guard passes)

    Cleanup is split into two separate paths:
      Startup failure — tears down only what was opened, without entering
          the normal SSE draining path.
      Normal shutdown — full teardown including draining and change stream
    """
    _redis_runtime = None
    _redis_service = None
    _redis_streams_service = None
    _leader = None
    _relay_svc = None
    _agent_deps = None
    _delivery_facade = None
    _delivery_config = None
    _execution_deps = None
    _mongo_dal = None
    _delivery_bound = False
    _bg_started = False

    try:
        # ── Phase 1: Infrastructure (DB + Redis, no background work) ──


        mongo_dal = create_mongo_dal()
        _mongo_dal = mongo_dal
        app.state.mongo_dal = mongo_dal
        await mongo_dal.connect()

        if await mongo_dal.ping():
            from a2a_adapter import AgentCardResolverImpl, AgentTransportImpl
            from a2a_adapter import artifact_storage as a2a_artifact_storage
            from app_shell.agent_capability_issue_service import (
                CapabilityIssueExclusionReader,
                capability_issue_service,
            )
            from app_shell.agent_matcher import agent_matcher
            from app_shell.agent_resolver_service import agent_resolver_service
            from app_shell.agent_runtime import AppShellAgentCenter
            from app_shell.agent_selection_service import agent_selection_service
            from app_shell.agent_service import agent_service
            from app_shell.bedrock_service import bedrock_service
            from app_shell.compaction_service import compaction_service
            from app_shell.context_memory_runtime import AppShellMemoryCenter
            from app_shell.debate_service import debate_service
            from app_shell.gemini_service import gemini_service
            from app_shell.inspection_runtime import AppShellInspectionCenter
            from app_shell.memory_service import (
                chat_memory_service,
                room_memory_service,
            )
            from app_shell.notification_service import notification_service
            from app_shell.openai_service import openai_service
            from app_shell.room_coordinator_service import room_coordinator_service
            from app_shell.room_membership_source import LegacyRoomMembershipSeedSource
            from app_shell.room_runtime import (
                AppShellRoomCenter,
                build_turn_content,
                room_runtime,
                room_services,
            )
            from app_shell.task_service import task_service
            from common.utils.a2a_helpers import bind_a2a_artifact_storage
            from context_memory.config import ContextMemoryLLMConfig
            from execution.orchestration.room_supervisor_service import (
                SupervisorPlanningError,
                room_supervisor_service,
            )
            from llm_gateway import LLMGatewayImpl, ModelRegistryImpl
            from llm_gateway.config import LLMGatewayConfig
            from llm_gateway.services import (
                AgentSelectionLLMService,
                DebateLLMService,
                DiscoveryLLMService,
                EmbeddingLLMService,
                MessageParserLLMService,
                RoomMemoryLLMService,
                SummaryLLMService,
                SupervisorLLMService,
            )
            from platform_module import (
                PlatformAgentAvatarManager,
                PlatformAttachmentCleanupPort,
                PlatformAttachmentMetadataReader,
                PlatformObjectStorage,
            )
            from platform_module.adapters import RateLimitCollectionAdapter
            from platform_module.rate_limit import PlatformAgentRateLimiter

            object_storage = create_object_storage_dal()
            platform_object_storage = PlatformObjectStorage(
                object_storage,
                default_presigned_url_ttl=runtime.settings.s3_presigned_url_ttl,
            )
            a2a_artifact_storage.bind_a2a_storage_dependencies(
                storage_service=platform_object_storage,
                s3_bucket_name=runtime.settings.s3_bucket_name,
                max_file_size_mb=runtime.settings.max_file_size_mb,
            )
            bind_a2a_artifact_storage(a2a_artifact_storage)
            await ensure_runtime_indexes(mongo=mongo_dal)
            agent_rate_limiter = PlatformAgentRateLimiter(
                collection=RateLimitCollectionAdapter(
                    mongo_dal.collection("agent_requests"),
                    "agent_requests",
                ),
            )
            viewset.bind_viewset_dependencies(
                provider=AppShellDALViewSetRepositoryProvider(mongo=mongo_dal),
            )

            inspection_center.bind_inspection_dependencies(AppShellInspectionCenter())
            memory_center.bind_memory_dependencies(AppShellMemoryCenter())
            # Bind Platform-owned API key store after MongoDAL is created
            api_key_store = create_api_key_store(mongo=mongo_dal)
            discovery_api_keys.bind_api_key_store(api_key_store)
            bind_api_key_authenticator(MongoAPIKeyAuthenticator(api_key_store))
            vector_dal = create_vector_dal()
            _delivery_config = create_delivery_config(runtime.settings)
            delivery_startup_policy = create_delivery_startup_policy(
                redis_url=runtime.settings.redis_url,
                multi_worker=runtime.settings.is_gunicorn,
            )
            delivery_redis_kv, delivery_redis_pubsub = create_delivery_redis_clients(
                redis_url=runtime.settings.redis_url,
                config=_delivery_config,
            )
            cancellation_collection = create_delivery_cancellation_collection(
                mongo=mongo_dal
            )
            _delivery_facade = create_delivery_facade(
                cancellation_collection=cancellation_collection,
                startup_policy=delivery_startup_policy,
                redis_kv=delivery_redis_kv,
                redis_pubsub=delivery_redis_pubsub,
                config=_delivery_config,
            )
            await _delivery_facade.start()
            sse_manager.bind_facade(_delivery_facade)
            _delivery_bound = True
            _delivery_deps = create_delivery_deps(_delivery_facade)
            app.state.delivery_facade = _delivery_facade
            app.state.delivery_deps = _delivery_deps

            from a2a_adapter.task_status import coerce_task_state
            from app_shell.a2a_runtime import (
                A2ARuntimeConfig,
                a2a_service,
            )
            from app_shell.hitl_service import (
                bind_hitl_service,
                create_hitl_service,
                hitl_service,
            )

            capability_issue_service.bind_mongo(mongo_dal)
            from common.observability.run_metrics import increment_counter
            from execution.cancellation import (
                AgentTaskCleanupAdapter,
                CancellationStateC3Adapter,
                HITLMessageCancellationAdapter,
                MongoCancellationStoreAdapter,
            )
            from execution.client_request_id import SSEClientRequestIdResolver
            from execution.dispatch.response_handler import AgentResponseHandler
            from execution.dispatch.task_notifications import (
                TaskNotificationAdapter,
                _notify_task_update_impl,
                bind_notification_store,
                bind_task_notification_runtime,
                notify_task_update,
            )
            from execution.dispatch.task_notifications import (
                bind_processing_status_emitter as bind_task_processing_status_emitter,
            )
            from execution.dispatch.transports.webhook import WebhookTransport
            from execution.events import (
                emit_processing_status,
                run_event_notification_from_payload,
            )
            from execution.events import (
                emit_room_processing_status as emit_execution_room_processing_status,
            )
            from execution.hitl.adapters import (
                A2AHITLContinuationAdapter,
                HITLDeliveryAdapter,
                HITLTaskNotificationAdapter,
            )
            from execution.orchestration.factory import (
                create_room_message_center,
            )
            from execution.orchestration.factory import (
                room_message_center as execution_room_message_center,
            )
            from execution.run_command_handler import (
                RunCommandHandler,
                run_event_sse_enabled,
            )
            from execution.run_lifecycle import RunLifecycleAdapter
            from execution.run_lifecycle_service import bind_run_lifecycle_service
            from execution.run_queries import RunQueryAdapter
            from models.quote import QuotedSnippet

            _execution_repos = create_execution_repositories(mongo=mongo_dal)
            run_command_handler = RunCommandHandler(
                run_repository=_execution_repos["run_repository"],
                run_event_repository=_execution_repos["run_event_repository"],
            )
            bind_run_lifecycle_service(run_command_handler)

            async def notify_task_update_with_string_state(**kwargs):
                state = kwargs.get("state")
                kwargs["state"] = coerce_task_state(state)
                return await notify_task_update(**kwargs)

            run_lifecycle = RunLifecycleAdapter(
                command_handler=run_command_handler,
                run_repository=_execution_repos["run_repository"],
            )
            app.state.execution_run_lifecycle = run_lifecycle

            model_registry = ModelRegistryImpl()
            llm_gateway_config = LLMGatewayConfig.from_settings(runtime.settings)
            llm_provider = LLMGatewayImpl(
                model_registry=model_registry,
                config=llm_gateway_config,
            )
            supervisor_llm_service = SupervisorLLMService(
                llm_provider=llm_provider,
                default_model=llm_gateway_config.default_supervisor_model,
            )
            embedding_llm_service = EmbeddingLLMService(llm_provider=llm_provider)
            discovery_llm_service = DiscoveryLLMService(
                llm_provider=llm_provider,
                max_expansion_words=runtime.settings.discovery_query_expansion_threshold,
            )
            summary_llm_service = SummaryLLMService(llm_provider=llm_provider)
            agent_selection_llm_service = AgentSelectionLLMService(
                llm_provider=llm_provider
            )
            debate_llm_service = DebateLLMService(llm_provider=llm_provider)
            message_parser_llm_service = MessageParserLLMService(
                llm_provider=llm_provider
            )
            room_memory_llm_service = RoomMemoryLLMService(llm_provider=llm_provider)
            openai_service.bind_llm_gateway(
                llm_provider,
                llm_gateway_config,
                discovery_query_expansion_threshold=(
                    runtime.settings.discovery_query_expansion_threshold
                ),
                debate_rounds=runtime.settings.debate_rounds,
            )
            gemini_service.bind_llm_gateway(llm_provider)
            bedrock_service.bind_llm_services(
                supervisor_service=supervisor_llm_service,
                llm_provider=llm_provider,
                llm_gateway_config=llm_gateway_config,
            )
            room_supervisor_service.bind_supervisor_service(supervisor_llm_service)
            room_memory_service.bind_turn_notes_llm_provider(llm_provider)
            chat_memory_service.bind_room_memory_llm_service(room_memory_llm_service)
            room_runtime.bind_message_parser_service(message_parser_llm_service)
            room_runtime.bind_debate_rounds(runtime.settings.debate_rounds)
            agent_resolver_service.bind_agent_selection_service(
                agent_selection_llm_service
            )
            room_coordinator_service.bind_summary_service(summary_llm_service)
            openai_service.bind_debate_service(debate_llm_service)
            agent_viewset.bind_agent_viewset_dependencies(
                embedding_source=embedding_llm_service,
                vector_index_service=create_agent_viewset_vector_index(
                    vector=vector_dal
                ),
            )
            agent_card_resolver = AgentCardResolverImpl()
            _agent_deps = create_agent_deps(
                mongo=mongo_dal,
                vector=vector_dal,
                llm_provider=llm_provider,
                card_resolver=agent_card_resolver,
                hub_liveness=None,
                exclusion_reader=CapabilityIssueExclusionReader(),
                gateway_base_url=runtime.settings.gateway_base_url,
            )
            _agent_facade = _agent_deps.agent_registry
            agent_service.bind_facade(_agent_facade)
            agent_matcher.bind_facade(_agent_facade)
            agent_selection_service.bind_facade(_agent_facade)
            agent_health_service.bind_repository(_agent_deps.agent_repository)
            agent.bind_agent_dependencies(
                center=AppShellAgentCenter(),
                service=agent_service,
                issue_service=capability_issue_service,
                avatar_manager=PlatformAgentAvatarManager(
                    platform_object_storage,
                    _agent_deps.agent_repository,
                ),
            )
            _resolver_repo = create_agent_resolver_repository(service=agent_service)
            agent_resolver_service.bind_repository(_resolver_repo)
            from agent.domain_alias import DomainAliasService as _DomainAliasSvc
            from app_shell.domain_alias_service import (
                bind_domain_alias_service as _bind_domain_alias,
            )

            _bind_domain_alias(_DomainAliasSvc(repository=_agent_deps.agent_repository))

            membership_source = LegacyRoomMembershipSeedSource()
            _room_deps = create_room_deps(
                mongo=mongo_dal,
                agent_registry=_agent_deps.agent_registry,
                membership_source=membership_source,
            )
            _room_facade = _room_deps.room_registry
            # Compatibility store: callers below still receive the composite object while
            # AppShellRepositoryStore delegates to focused runtime store parts. Do not add
            # new broad-store consumers; bind a focused part or protocol instead.
            app_shell_store = create_app_shell_repository_store(
                mongo=mongo_dal,
                room_deps=_room_deps,
                agent_deps=_agent_deps,
            )
            agent_room_store = app_shell_store.agent_room
            message_store = app_shell_store.messages
            task_store = app_shell_store.tasks
            hitl_store = app_shell_store.hitl
            memory_store = app_shell_store.memory
            max_tasks_per_user = app_shell_store.MAX_TASKS_PER_USER
            max_tasks_per_room = app_shell_store.MAX_TASKS_PER_ROOM

            # Transitional P3 adapters: keep startup wiring narrow without
            # introducing long-lived app-shell classes in this slice. Follow-up
            # hardening can replace these SimpleNamespace seams with concrete
            # protocol adapters when static type enforcement becomes the goal.
            async def check_task_limits(
                user_id: str,
                room_id: str,
                non_terminal_states: list[str],
            ) -> None:
                await task_store.check_task_limits(
                    user_id,
                    room_id,
                    non_terminal_states,
                    max_tasks_per_user=max_tasks_per_user,
                    max_tasks_per_room=max_tasks_per_room,
                )

            task_notification_store = SimpleNamespace(
                update_last_notified_state=message_store.update_last_notified_state,
                get_room_agent_message_by_message_id=(
                    message_store.get_room_agent_message_by_message_id
                ),
                update_room_agent_message_by_message_id=(
                    message_store.update_room_agent_message_by_message_id
                ),
                get_room_by_room_id=agent_room_store.get_room_by_room_id,
                resolve_client_request_id_for_agent_message=(
                    task_store.resolve_client_request_id_for_agent_message
                ),
            )
            a2a_task_tracking_store = SimpleNamespace(
                check_task_limits=check_task_limits,
                generate_webhook_token=task_store.generate_webhook_token,
                hash_webhook_token=task_store.hash_webhook_token,
                enable_task_tracking_on_message=(
                    task_store.enable_task_tracking_on_message
                ),
                get_room_agent_message_by_message_id=(
                    message_store.get_room_agent_message_by_message_id
                ),
                update_webhook_token_hash_on_message=(
                    task_store.update_webhook_token_hash_on_message
                ),
                get_agent_by_agent_id=agent_room_store.get_agent_by_agent_id,
                update_task_on_message=task_store.update_task_on_message,
            )
            hitl_runtime_store = SimpleNamespace(
                count_hitl_requests_for_message=(
                    hitl_store.count_hitl_requests_for_message
                ),
                create_hitl_request=hitl_store.create_hitl_request,
                update_agent_message_task_state=(
                    hitl_store.update_agent_message_task_state
                ),
                persist_hitl_user_answer=hitl_store.persist_hitl_user_answer,
                persist_hitl_group_metadata=hitl_store.persist_hitl_group_metadata,
                get_hitl_request=hitl_store.get_hitl_request,
                claim_hitl_request=hitl_store.claim_hitl_request,
                fenced_update_hitl_request=hitl_store.fenced_update_hitl_request,
                count_pending_in_hitl_group=hitl_store.count_pending_in_hitl_group,
                get_hitl_group_requests=hitl_store.get_hitl_group_requests,
                release_hitl_group_routing=hitl_store.release_hitl_group_routing,
                claim_hitl_group_routing=hitl_store.claim_hitl_group_routing,
                reset_last_notified_state=message_store.reset_last_notified_state,
                get_room_agent_message_by_message_id=(
                    message_store.get_room_agent_message_by_message_id
                ),
                get_pending_continuation_on_message=(
                    task_store.get_pending_continuation_on_message
                ),
                save_continuation_on_user_message=(
                    task_store.save_continuation_on_user_message
                ),
                get_pending_hitl_requests=hitl_store.get_pending_hitl_requests,
                get_pending_hitl_requests_for_message=(
                    hitl_store.get_pending_hitl_requests_for_message
                ),
                cas_update_hitl_request=hitl_store.cas_update_hitl_request,
                get_and_clear_continuation_on_message=(
                    task_store.get_and_clear_continuation_on_message
                ),
                get_and_clear_continuation_on_user_message=(
                    task_store.get_and_clear_continuation_on_user_message
                ),
                iter_stale_processing_hitl_requests=(
                    hitl_store.iter_stale_processing_hitl_requests
                ),
            )
            response_client_request_resolver = SimpleNamespace(
                resolve_client_request_id_for_message_id=(
                    task_store.resolve_client_request_id_for_message_id
                ),
                get_room_agent_message_by_message_id=(
                    message_store.get_room_agent_message_by_message_id
                ),
                resolve_client_request_id_for_agent_message=(
                    task_store.resolve_client_request_id_for_agent_message
                ),
            )
            stale_task_store = SimpleNamespace(
                get_stale_task_messages=task_store.get_stale_task_messages,
                get_expired_task_messages=task_store.get_expired_task_messages,
                get_non_tracked_stale_task_messages=(
                    task_store.get_non_tracked_stale_task_messages
                ),
                find_stale_non_terminal_runs=task_store.find_stale_non_terminal_runs,
                touch_task_message=task_store.touch_task_message,
                is_message_cancelled=task_store.is_message_cancelled,
                update_task_on_message=task_store.update_task_on_message,
                get_and_clear_continuation_on_message=(
                    task_store.get_and_clear_continuation_on_message
                ),
                get_and_clear_continuation_on_user_message=(
                    task_store.get_and_clear_continuation_on_user_message
                ),
                get_room_ids_with_non_terminal_runs=(
                    task_store.get_room_ids_with_non_terminal_runs
                ),
                get_orphaned_agent_messages=task_store.get_orphaned_agent_messages,
                get_agent_by_agent_id=agent_room_store.get_agent_by_agent_id,
                get_stuck_supervisor_trajectory_messages=(
                    task_store.get_stuck_supervisor_trajectory_messages
                ),
                claim_stuck_supervisor_trajectory=(
                    task_store.claim_stuck_supervisor_trajectory
                ),
            )
            debate_message_store = SimpleNamespace(
                get_room_agent_message_by_message_id=(
                    message_store.get_room_agent_message_by_message_id
                ),
                get_agent_name_by_agent_id=agent_room_store.get_agent_name_by_agent_id,
                update_room_agent_message_with_new_message_content_by_message_id=(
                    message_store.update_room_agent_message_with_new_message_content_by_message_id
                ),
            )
            room_coordinator_message_store = SimpleNamespace(
                get_room_by_room_id=agent_room_store.get_room_by_room_id,
                get_agent_name_by_agent_id=agent_room_store.get_agent_name_by_agent_id,
                get_room_user_message_by_message_id=(
                    message_store.get_room_user_message_by_message_id
                ),
                get_room_agent_messages_by_related_message_id=(
                    message_store.get_room_agent_messages_by_related_message_id
                ),
                add_room_agent_message=message_store.add_room_agent_message,
            )
            room_runtime_store = SimpleNamespace(
                add_room_agent_message=message_store.add_room_agent_message,
                get_agent_by_agent_id=agent_room_store.get_agent_by_agent_id,
                get_agent_group_by_id=agent_room_store.get_agent_group_by_id,
                get_agents_with_conditions=(
                    agent_room_store.get_agents_with_conditions
                ),
                get_all_active_agents=agent_room_store.get_all_active_agents,
                get_room_by_room_id=agent_room_store.get_room_by_room_id,
                get_room_memory_by_room_id=memory_store.get_room_memory_by_room_id,
                get_room_user_message_by_message_id=(
                    message_store.get_room_user_message_by_message_id
                ),
                update_room_by_room_id=agent_room_store.update_room_by_room_id,
                update_room_user_message_by_message_id=(
                    message_store.update_room_user_message_by_message_id
                ),
            )
            relay_runtime_store = SimpleNamespace(
                get_agent_by_agent_id=agent_room_store.get_agent_by_agent_id,
                get_room_agent_message_by_message_id=(
                    message_store.get_room_agent_message_by_message_id
                ),
                get_room_by_room_id=agent_room_store.get_room_by_room_id,
                get_room_user_message_by_message_id=(
                    message_store.get_room_user_message_by_message_id
                ),
                increment_agent_call_count=agent_room_store.increment_agent_call_count,
                is_message_cancelled=task_store.is_message_cancelled,
            )
            async def get_quoted_snippet_by_id(quote_id: str):
                quote_doc = await _room_deps.room_quote_repository.get_by_id(quote_id)
                return (
                    QuotedSnippet.model_validate(quote_doc)
                    if quote_doc is not None
                    else None
                )

            execution_message_reader = SimpleNamespace(
                get_quoted_snippet_by_id=get_quoted_snippet_by_id,
                get_room_agent_message_by_message_id=(
                    message_store.get_room_agent_message_by_message_id
                ),
                get_room_agent_messages_by_related_message_id=(
                    message_store.get_room_agent_messages_by_related_message_id
                ),
                get_room_user_message_by_message_id=(
                    message_store.get_room_user_message_by_message_id
                ),
            )
            execution_message_writer = SimpleNamespace(
                accumulate_artifact_on_message=(
                    message_store.accumulate_artifact_on_message
                ),
                add_room_agent_message=message_store.add_room_agent_message,
                cancel_agent_messages_by_ids=(
                    message_store.cancel_agent_messages_by_ids
                ),
                cancel_descendants=message_store.cancel_descendants,
                claim_or_reclaim_user_message=(
                    message_store.claim_or_reclaim_user_message
                ),
                claim_user_message_for_processing=(
                    message_store.claim_user_message_for_processing
                ),
                delete_room_agent_message_by_message_id=(
                    message_store.delete_room_agent_message_by_message_id
                ),
                refresh_processing_claim=message_store.refresh_processing_claim,
                reset_last_notified_state=message_store.reset_last_notified_state,
                turn_exists=message_store.turn_exists,
                unclaim_user_message=message_store.unclaim_user_message,
                update_last_notified_state=message_store.update_last_notified_state,
                update_room_agent_message_by_message_id=(
                    message_store.update_room_agent_message_by_message_id
                ),
                update_room_agent_message_with_new_message_content_by_message_id=(
                    message_store.update_room_agent_message_with_new_message_content_by_message_id
                ),
                update_room_user_message_by_message_id=(
                    message_store.update_room_user_message_by_message_id
                ),
                update_task_state_on_message=message_store.update_task_state_on_message,
                upsert_room_agent_message=message_store.upsert_room_agent_message,
            )
            execution_task_state_store = SimpleNamespace(
                enable_task_tracking_on_message=(
                    task_store.enable_task_tracking_on_message
                ),
                is_message_cancelled=task_store.is_message_cancelled,
                resolve_client_request_id_for_agent_message=(
                    task_store.resolve_client_request_id_for_agent_message
                ),
                resolve_client_request_id_for_message_id=(
                    task_store.resolve_client_request_id_for_message_id
                ),
                update_task_on_message=task_store.update_task_on_message,
            )
            execution_continuation_store = SimpleNamespace(
                get_and_clear_continuation_on_message=(
                    task_store.get_and_clear_continuation_on_message
                ),
                get_and_clear_continuation_on_user_message=(
                    task_store.get_and_clear_continuation_on_user_message
                ),
                get_pending_continuation_on_message=(
                    task_store.get_pending_continuation_on_message
                ),
                save_continuation_on_message=task_store.save_continuation_on_message,
                save_continuation_on_user_message=(
                    task_store.save_continuation_on_user_message
                ),
            )
            execution_agent_lookup = SimpleNamespace(
                get_agent_by_agent_id=agent_room_store.get_agent_by_agent_id,
            )
            execution_agent_group_reader = SimpleNamespace(
                get_agent_group_by_id=agent_room_store.get_agent_group_by_id,
            )
            execution_room_reader = SimpleNamespace(
                get_agent_by_agent_id=agent_room_store.get_agent_by_agent_id,
                get_agent_group_by_id=agent_room_store.get_agent_group_by_id,
                get_agent_name_by_agent_id=agent_room_store.get_agent_name_by_agent_id,
                get_room_by_room_id=agent_room_store.get_room_by_room_id,
            )
            execution_room_writer = SimpleNamespace(
                update_room_by_room_id=agent_room_store.update_room_by_room_id,
            )
            execution_memory_reader = SimpleNamespace(
                get_room_memory_by_room_id=memory_store.get_room_memory_by_room_id,
            )
            execution_memory_writer = SimpleNamespace()
            execution_hitl_reader = SimpleNamespace(
                get_pending_hitl_requests_for_message=(
                    hitl_store.get_pending_hitl_requests_for_message
                ),
            )
            execution_coordinator = SimpleNamespace(
                emit_synthesis_message=room_coordinator_service.emit_synthesis_message,
            )

            membership_source.bind_store(agent_room_store)
            debate_service.bind_store(debate_message_store)
            room_coordinator_service.bind_store(room_coordinator_message_store)
            chat_memory_service.bind_store(memory_store)
            room_memory_service.bind_store(memory_store)
            bind_notification_store(task_notification_store)
            bind_task_notification_runtime(
                notification_service=notification_service,
                delivery=sse_manager,
            )
            a2a_service.bind_runtime_config(
                A2ARuntimeConfig(webhook_base_url=runtime.settings.webhook_base_url)
            )
            a2a_service.bind_task_db(
                a2a_task_tracking_store,
                call_counter=agent_room_store,
            )
            app_shell_client_request_id_resolver = SSEClientRequestIdResolver(
                resolver=task_store,
            )
            app.state.execution_client_request_id_resolver = (
                app_shell_client_request_id_resolver
            )
            bind_hitl_service(
                create_hitl_service(
                    persistence=hitl_runtime_store,
                    delivery=HITLDeliveryAdapter(_delivery_deps.event_publisher),
                    agent_reply=A2AHITLContinuationAdapter(
                        a2a_service,
                        lambda: execution_room_message_center,
                    ),
                    continuation=A2AHITLContinuationAdapter(
                        a2a_service,
                        lambda: execution_room_message_center,
                    ),
                    task_notifications=HITLTaskNotificationAdapter(
                        notify_task_update_with_string_state
                    ),
                )
            )
            route_room_reader = SimpleNamespace(
                get_room_by_room_id=agent_room_store.get_room_by_room_id,
            )
            a2a_task_status_reader = SimpleNamespace(
                get_room_agent_message_by_message_id=(
                    message_store.get_room_agent_message_by_message_id
                ),
                get_task_messages_for_room=task_store.get_task_messages_for_room,
                get_pending_task_messages_for_user=(
                    task_store.get_pending_task_messages_for_user
                ),
            )
            sse_state_reader = SimpleNamespace(
                get_room_by_room_id=agent_room_store.get_room_by_room_id,
                get_room_user_message_by_message_id=(
                    message_store.get_room_user_message_by_message_id
                ),
            )
            room_center.bind_room_dependencies(
                center=AppShellRoomCenter(),
                store=route_room_reader,
                selection_service=agent_selection_service,
            )
            a2a_tasks.bind_a2a_task_dependencies(a2a_task_status_reader)
            agent_group.bind_agent_group_dependencies(agent_room_store)
            sse.bind_sse_dependencies(sse_state_reader, sse_manager)
            room_runtime.bind_store(room_runtime_store)
            room_runtime.bind_facade(_room_facade)
            room_runtime.bind_object_storage(platform_object_storage)
            room_center.room_center.bind_facade(_room_facade)
            hitl.bind_room_ownership_reader(_room_facade)
            context_memory_facade = create_context_memory_facade(
                mongo=mongo_dal,
                vector=vector_dal,
                llm_provider=llm_provider,
                room_history_reader=_room_deps.room_history_reader,
                llm_config=ContextMemoryLLMConfig(
                    turn_notes_model="context_memory_legacy_json_model",
                    summary_model="context_memory_legacy_json_model",
                ),
            )
            _context_memory_deps = create_context_memory_deps(context_memory_facade)
            room_message_center_impl = create_room_message_center(
                room_runtime=room_services,
                message_reader=execution_message_reader,
                message_writer=execution_message_writer,
                task_state_store=execution_task_state_store,
                continuation_store=execution_continuation_store,
                agent_lookup=execution_agent_lookup,
                agent_group_reader=execution_agent_group_reader,
                room_reader=execution_room_reader,
                room_writer=execution_room_writer,
                memory_reader=execution_memory_reader,
                memory_writer=execution_memory_writer,
                hitl_reader=execution_hitl_reader,
                delivery=sse_manager,
                coordinator=execution_coordinator,
                summary_service=summary_llm_service,
                notification_service=notification_service,
                agent_resolver_service=agent_resolver_service,
                a2a_transport=a2a_service,
                remote_task_reader=task_service,
                room_memory=room_memory_service,
                debate_service=debate_service,
                rate_limit_service=agent_rate_limiter,
                room_supervisor_service=room_supervisor_service,
                hitl_coordinator=hitl_service,
                task_notifications=TaskNotificationAdapter(
                    notify_task_update_with_string_state
                ),
                task_notification_impl=_notify_task_update_impl,
                agent_health_service=agent_health_service,
                object_storage=platform_object_storage,
                capability_issue_service=capability_issue_service,
                context_memory_runtime=_context_memory_deps.context_memory_runtime,
                compaction_service=compaction_service,
                build_turn_content_func=build_turn_content,
                supervisor_planning_error_cls=SupervisorPlanningError,
                orphan_threshold_minutes=runtime.settings.orphan_threshold_minutes,
                debate_rounds=runtime.settings.debate_rounds,
                cloud_health_cache_ttl=runtime.settings.cloud_health_cache_ttl,
                cloud_health_check_timeout=runtime.settings.cloud_health_check_timeout,
            )
            execution_room_message_center.bind(room_message_center_impl)
            execution_room_message_center.bind_facade(_room_facade)

            def create_webhook_transport():
                handler = AgentResponseHandler(
                    message_writer=message_store,
                    task_writer=message_store,
                    continuation_store=task_store,
                    client_request_resolver=response_client_request_resolver,
                    room_reader=agent_room_store,
                    hitl_reader=hitl_store,
                    delivery=sse_manager,
                    room_message_center=execution_room_message_center,
                    hitl_coordinator=hitl_service,
                    notification_service=notification_service,
                    task_notification_impl=_notify_task_update_impl,
                )
                handler.bind_execution_event_deps(emit_room_processing_status)
                return WebhookTransport(
                    response_handler=handler,
                    webhook_auth=task_store,
                    message_reader=message_store,
                    cancellation_reader=task_store,
                    task_notifier=notify_task_update_with_string_state,
                )

            execution_facade = create_execution_facade(
                room_center=room_center.room_center,
                room_message_center=execution_room_message_center,
                hitl_service=hitl_service,
                run_lifecycle=run_lifecycle,
                run_reader=RunQueryAdapter(_execution_repos["run_repository"]),
                cancellation_state=CancellationStateC3Adapter(sse_manager),
                cancellation_store=MongoCancellationStoreAdapter(task_store),
                hitl_message_cancellation=HITLMessageCancellationAdapter(hitl_service),
                agent_task_cleanup=AgentTaskCleanupAdapter(
                    message_task_store=message_store,
                    get_agent_card_from_url=a2a_service.get_agent_card_from_url,
                    cancel_remote_task=a2a_service.cancel_remote_task,
                    notify_task_update=notify_task_update_with_string_state,
                ),
                agent_response_handler=execution_room_message_center.agent_response_handler,
                event_publisher=_delivery_deps.event_publisher,
                run_event_enabled=run_event_sse_enabled,
                client_request_id_resolver=app_shell_client_request_id_resolver,
            )
            _execution_deps = create_execution_deps(execution_facade)

            async def read_room_active_runs(room_id: str):
                runs = await execution_facade.get_runs_for_room(room_id)
                return [
                    {
                        "run_id": run.run_id,
                        "state": str(getattr(run.state, "value", run.state)),
                        "trigger_message_id": run.trigger_message_id,
                        "agent_id": run.agent_id,
                        "seq": run.seq,
                        "updated_at": run.updated_at,
                    }
                    for run in runs
                ]

            async def emit_room_processing_status(**kwargs):
                return await emit_execution_room_processing_status(
                    **kwargs,
                    run_lifecycle=run_lifecycle,
                    event_publisher=_delivery_deps.event_publisher,
                    run_event_enabled=run_event_sse_enabled,
                    client_request_id_resolver=app_shell_client_request_id_resolver,
                )

            webhooks.bind_webhook_dependencies(create_webhook_transport())
            bind_task_processing_status_emitter(emit_room_processing_status)
            room_runtime.bind_hitl_pending_checker(hitl_service.get_pending_requests)
            room_runtime.bind_active_run_reader(read_room_active_runs)
            room_runtime.bind_execution_event_deps(
                processing_status_emitter=emit_room_processing_status,
            )
            execution_room_message_center.bind_execution_event_deps(
                emit_room_processing_status
            )
            room_center.bind_execution_deps(_execution_deps)
            hitl.bind_execution_deps(_execution_deps)
            sse.bind_execution_deps(_execution_deps)
            app.state.execution_facade = execution_facade
            app.state.execution_deps = _execution_deps

            platform_config = create_platform_config(runtime.settings)
            platform_deps = create_platform_deps(
                agent_deps=_agent_deps,
                mongo=mongo_dal,
                agent_transport=AgentTransportImpl(),
                agent_card_resolver=agent_card_resolver,
                object_storage=object_storage,
                content_storage_repository=context_memory_facade.content_repository,
                discovery_query_expander=discovery_llm_service,
                logger=logger,
            )
            room_runtime.bind_attachment_metadata_reader(
                PlatformAttachmentMetadataReader(platform_deps.file_metadata_repository)
            )
            room_runtime.bind_attachment_cleanup(
                PlatformAttachmentCleanupPort(platform_deps.file_metadata_repository)
            )
            platform_facade = create_platform_facade(
                config=platform_config,
                deps=platform_deps,
            )

            gateway.bind_gateway_dependencies(
                platform_facade.gateway_service,
                platform_facade.gateway_rate_limiter,
            )
            discovery.bind_discovery_dependencies(
                platform_facade.discovery_service,
                platform_facade.discovery_rate_limiter,
                default_limit=runtime.settings.discovery_default_limit,
            )
            files.bind_file_dependencies(
                platform_facade.file_storage,
                _room_deps.room_registry,
            )
            app.state.platform_facade = platform_facade
            app.state.platform_deps = platform_deps
            # TODO(phase-6/7): Register ContextMemoryEventHandler with EventPublisher
            # once Delivery wires runtime MessageCommitted delivery. Phase 5 keeps the
            # direct compaction call path via legacy app_shell.
            compaction_service.bind_content_storage(platform_facade.content_storage)
            compaction_service.bind_room_memory_reader(context_memory_facade)
            compaction_service.bind_facade(context_memory_facade)
            room_memory_service.bind_facade(context_memory_facade)
            room_runtime.bind_context_memory(
                _context_memory_deps.memory_manager,
                _context_memory_deps.context_memory_runtime,
            )
        else:
            raise RuntimeError("MongoDAL ping failed after connect")

        if _execution_deps is None:
            raise RuntimeError("ExecutionDeps have not been bound")
        try:
            healed = await _execution_deps.execution_engine.heal_diverged_runs(
                limit=500
            )
        except Exception:
            logger.warning(
                "startup heal: failed; continuing startup", exc_info=True
            )
        else:
            if healed:
                logger.info("startup heal: healed %s diverged run(s)", healed)
        if runtime.settings.webhook_signing_key:
            await hitl_store.ensure_hitl_indexes()

        # Init app-shell Redis subsystems before the guard. Delivery-owned
        # Pub/Sub/KV clients are constructed through container.py above.
        from app_shell.redis_runtime import create_app_shell_redis_runtime

        _redis_runtime = create_app_shell_redis_runtime(
            instance_id=(
                _delivery_facade.instance_id
                if _delivery_facade is not None
                else sse_manager._instance_id
            )
        )
        _redis_service = _redis_runtime.command_client
        if _redis_service:
            await _redis_service.start()
            logger.info("App-shell Redis started (leader election/relay enabled)")
        else:
            logger.info("App-shell Redis disabled (REDIS_URL not set)")
        app.state.redis_runtime = _redis_runtime

        _redis_streams_service = _redis_runtime.streams_client
        if _redis_streams_service:
            await _redis_streams_service.start()

        # ── Guard: fail if gunicorn without fully connected Redis ──
        check_multi_worker_safety(
            is_gunicorn=runtime.settings.is_gunicorn,
            delivery_pubsub_connected=bool(
                _delivery_facade and _delivery_facade.delivery_pubsub_connected
            ),
            delivery_kv_connected=bool(
                _delivery_facade and _delivery_facade.delivery_kv_connected
            ),
            redis_service_connected=bool(
                _redis_service and _redis_service.is_connected
            ),
            relay_streams_connected=bool(
                _redis_streams_service and _redis_streams_service.is_connected
            ),
            change_stream_connected=bool(
                _delivery_facade and _delivery_facade.change_stream_connected
            ),
        )

        # ── Phase 2: Background services (only after guard passes) ──

        if _redis_service and _redis_service.is_connected:
            _leader = _redis_runtime.leader
            logger.info("Leader election enabled for background jobs")

        agent_health_service.set_leader_election(_leader)
        stale_task_checker.set_leader_election(_leader)
        stale_task_checker.configure_timing(
            stale_check_minutes=runtime.settings.stale_check_minutes,
            task_expiry_hours=runtime.settings.task_expiry_hours,
            pending_task_warning_hours=runtime.settings.pending_task_warning_hours,
            orphan_threshold_minutes=runtime.settings.orphan_threshold_minutes,
            processing_status_expiry_minutes=runtime.settings.processing_status_expiry_minutes,
        )
        stale_task_checker.set_runtime_deps(
            StaleTaskCheckerDeps(
                store=stale_task_store,
                rooms_collection=mongo_dal.collection("rooms"),
                notify_task_update=notify_task_update,
                increment_counter=increment_counter,
                a2a_service=a2a_service,
            )
        )
        if _execution_deps is not None:
            from jobs.stale_task_checker import (
                StaleHITLDeps,
                StaleRecoveryDeps,
                StaleRunWatchdogEventDeps,
            )

            def run_dual_write_enabled() -> bool:
                return runtime.settings.feature_run_dual_write

            async def emit_watchdog_run_event(
                *,
                room_id: str,
                payload: dict,
                client_request_id: str | None = None,
            ) -> None:
                if payload and run_event_sse_enabled():
                    await _delivery_deps.event_publisher.emit(
                        run_event_notification_from_payload(
                            room_id=room_id,
                            payload=payload,
                            correlation_id=client_request_id,
                        )
                    )

            async def emit_watchdog_processing_status(
                *,
                room_id: str,
                status: str,
                message_id: str,
                client_request_id: str | None = None,
                details: str | None = None,
            ) -> None:
                await emit_processing_status(
                    room_id=room_id,
                    status=status,
                    message_id=message_id,
                    lifecycle_message_id=message_id,
                    record_lifecycle=False,
                    client_request_id=client_request_id,
                    details={"message": details} if details else None,
                    run_lifecycle=run_lifecycle,
                    event_publisher=_delivery_deps.event_publisher,
                    run_event_enabled=run_event_sse_enabled,
                    client_request_id_resolver=app_shell_client_request_id_resolver,
                )

            stale_task_checker.set_execution_recovery_deps(
                StaleRecoveryDeps(
                    schedule_recovery=execution_facade.schedule_recovery_orchestration,
                )
            )
            stale_task_checker.set_hitl_deps(
                StaleHITLDeps(
                    recover_stale_processing=hitl_service.recover_stale_processing,
                    cancel_requests_for_message=hitl_service.cancel_requests_for_message,
                )
            )
            stale_task_checker.set_run_watchdog_event_deps(
                StaleRunWatchdogEventDeps(
                    append_run_timeout_failure=run_lifecycle.append_run_timeout_failure,
                    emit_run_event=emit_watchdog_run_event,
                    emit_processing_status=emit_watchdog_processing_status,
                    run_dual_write_enabled=run_dual_write_enabled,
                )
            )
        compaction_sweep.set_leader_election(_leader)
        compaction_sweep.set_sweep_deps(
            CompactionSweepDeps(
                list_room_ids_with_memory=(
                    context_memory_facade.memory_repository.list_room_ids_with_memory
                ),
                get_room_ids_with_non_terminal_runs=(
                    _execution_repos[
                        "run_repository"
                    ].get_room_ids_with_non_terminal_runs
                ),
                compaction_service=compaction_service,
            )
        )
        orphaned_upload_cleaner.set_leader_election(_leader)
        orphaned_upload_cleaner.set_cleanup_deps(
            OrphanedUploadCleanerDeps(
                file_uploads_collection=mongo_dal.collection("file_uploads"),
                room_user_messages_collection=mongo_dal.collection("room_user_messages"),
                object_storage=platform_object_storage,
            )
        )

        _bg_started = True
        await agent_health_service.start()

        if runtime.settings.webhook_signing_key:
            await stale_task_checker.start()
            await stale_task_checker.check_stale_tasks()
            logger.info(
                "A2A long-running tasks support initialized (using room_agent_messages)"
            )
        else:
            logger.warning(
                "WEBHOOK_SIGNING_KEY not set - A2A long-running tasks disabled"
            )

        await compaction_sweep.start()
        await orphaned_upload_cleaner.start()

        # Initialize relay service
        from app_shell.agent_liveness_service import (
            bind_agent_liveness_deps,
            check_and_sync_liveness,
        )
        from app_shell.execution_runtime import get_bound_room_message_center
        from app_shell.relay_service import (
            RelayHubLivenessReader,
            init_relay_service,
        )
        from app_shell.relay_store import AppShellRelayHubStore
        from app_shell.room_lock import RedisRoomDistributedLock
        from execution.facade import hub_agent_response_internal_to_agent_event
        from hub_runtime_bridge.adapters.legacy_failure import (
            RelayOfflineFailureAdapter,
        )
        from hub_runtime_bridge.config import config_from_settings
        from hub_runtime_bridge.repository.mongo import HubMongoRepository

        _rmc = get_bound_room_message_center()
        _rmc.set_room_distributed_lock(RedisRoomDistributedLock(_redis_service))
        relay_hub_store = AppShellRelayHubStore(
            mongo=mongo_dal,
            hub_repository=HubMongoRepository(mongo_dal),
            agent_repository=_agent_deps.agent_repository,
        )
        _relay_svc = init_relay_service(
            mongo=relay_hub_store,
            db=relay_runtime_store,
            sse_manager=sse_manager,
            room_message_center=_rmc,
            hitl_coordinator=hitl_service,
            event_publisher=_delivery_deps.event_publisher if _delivery_deps else None,
            worker_id=(
                _delivery_facade.instance_id if _delivery_facade is not None else None
            ),
            response_converter=hub_agent_response_internal_to_agent_event,
            offline_failure_port=RelayOfflineFailureAdapter(
                message_store,
                sse_manager,
            ),
            config=config_from_settings(settings),
        )
        app.state.relay_service = _relay_svc
        relay.bind_relay_dependencies(_relay_svc)
        hub.bind_hub_dependencies(_relay_svc)
        if _delivery_deps is not None:
            router = _relay_svc.internal_response_dispatcher
            if router is None:
                raise RuntimeError("Hub internal response router is not bound")
            _delivery_deps.event_publisher.register_internal_handler(
                "hub_agent_response_internal",
                router.dispatch_hub_internal_response,
            )
        _relay_svc.set_leader_election(_leader)
        if _agent_deps is not None:
            hub_liveness_reader = RelayHubLivenessReader(_relay_svc)
            if hasattr(_agent_deps.agent_registry, "bind_hub_liveness"):
                _agent_deps.agent_registry.bind_hub_liveness(hub_liveness_reader)
            _relay_svc.bind_agent_registry_writer(_agent_deps.agent_registry_writer)
            bind_agent_liveness_deps(
                hub_liveness_reader=hub_liveness_reader,
                agent_registry_writer=_agent_deps.agent_registry_writer,
            )
            agent.bind_agent_liveness_checker(check_and_sync_liveness)
        await _relay_svc.start()
        logger.info("Relay service initialized and heartbeat checker started")

        # Attach Redis Streams to relay service
        if _redis_streams_service and _redis_streams_service.is_connected:
            _relay_streams = _redis_runtime.relay_streams
            _relay_svc.set_stream_service(_relay_streams)
            logger.info(
                "Redis Streams relay enabled (separate pool for blocking XREAD)"
            )

        bind_api_gateway_deps(
            APIGatewayDeps(
                gateway_service=getattr(gateway, "gateway_service", None),
                file_storage=getattr(files, "file_storage", None),
                relay_service=getattr(relay, "relay_service", None),
                execution_deps=getattr(app.state, "execution_deps", None),
                platform_facade=getattr(app.state, "platform_facade", None),
            )
        )

    except BaseException:
        # ── Startup failure: tear down only what was opened ──
        # Do not call set_draining() on startup failure; normal shutdown owns
        # the drain window after the adapter has been successfully bound.
        if _relay_svc:
            await _relay_svc.stop()
        if _redis_streams_service:
            await _redis_streams_service.stop()
        if _bg_started:
            await stale_task_checker.stop()
            await compaction_sweep.stop()
            await orphaned_upload_cleaner.stop()
            await agent_health_service.stop()
        if _leader:
            await _leader.release_all(ALL_JOB_NAMES)
        if _redis_service:
            await _redis_service.stop()
        if _mongo_dal is not None:
            await _mongo_dal.close()
            app.state.mongo_dal = None
        try:
            if _delivery_facade is not None:
                await _delivery_facade.stop()
        finally:
            if _delivery_bound:
                sse_manager.unbind_facade()
            app.state.delivery_facade = None
        raise

    # ── Phase 3: Serve + Normal Shutdown ──
    try:
        yield
    finally:
        # Stop the relay service heartbeat checker
        from app_shell.relay_service import relay_service as _relay_svc_shutdown

        if _relay_svc_shutdown:
            await _relay_svc_shutdown.stop()

        # Stop Redis Streams service (hub relay)
        if _redis_streams_service:
            await _redis_streams_service.stop()

        # Stop background services
        await stale_task_checker.stop()
        await compaction_sweep.stop()
        await orphaned_upload_cleaner.stop()
        await agent_health_service.stop()

        # Release any leader locks
        if _leader:
            await _leader.release_all(ALL_JOB_NAMES)

        execution_deps = app.state.execution_deps
        cancelled = await execution_deps.execution_engine.cancel_inflight_tasks()
        if cancelled:
            logger.info(
                "shutdown: cancelled %s in-flight execution task(s)",
                cancelled,
            )

        # Drain: stop accepting new SSE connections and allow in-flight events to finish
        if _delivery_bound:
            sse_manager.set_draining(True)
        await asyncio.sleep(
            _delivery_config.shutdown_drain_seconds
            if _delivery_config is not None
            else runtime.settings.shutdown_drain_seconds
        )

        try:
            if _delivery_facade is not None:
                await _delivery_facade.stop()
        finally:
            if _delivery_bound:
                sse_manager.unbind_facade()
            app.state.delivery_facade = None

        # Stop RedisService
        if _redis_service:
            await _redis_service.stop()
        if _mongo_dal is not None:
            await _mongo_dal.close()
            app.state.mongo_dal = None


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
    context_memory_runtime: ContextMemoryRuntime


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


def create_agent_viewset_vector_index(
    *, vector: VectorDAL
) -> AgentViewsetVectorIndexAdapter:
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
                candidates = [
                    agent for agent in candidates if agent.agent_id in allowed
                ]
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
        presigned_url_ttl_seconds=getattr(app_settings, "s3_presigned_url_ttl", 3600),
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
            status.strip() for status in terminal_statuses.split(",") if status.strip()
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
        context_memory_runtime=facade,
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
