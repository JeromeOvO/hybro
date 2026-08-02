from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agent import AgentFacade, AgentMongoRepository
from api_gateway.dependencies import (
    APIGatewayDeps,
    bind_api_gateway_deps,
    missing_required_deps,
)
from api_gateway.viewsets.repository import DALViewSetRepositoryProvider
from common.config.settings import settings
from common.health_check import RuntimeHealthCheck
from common.idempotency import MAX_CLIENT_REQUEST_ID_LENGTH
from common.observability import (
    MetricsCollector,
    get_instance_id,
    get_logger,
    traced_create_task,
)
from common.protocols import (
    AgentCallCounter,
    AgentCardResolver,
    AgentExclusionReader,
    AgentManagement,
    AgentMatcher,
    AgentRegistry,
    AgentRegistryWriter,
    AgentRepository,
    AttachmentMetadataReader,
    ContentStorageRepository,
    ContextAssembler,
    ContextMemoryRuntime,
    EventPublisher,
    ExecutionEngine,
    FileStorage,
    HITLManager,
    HubAgentResponseSink,
    HubDispatchPolicy,
    HubDispatchPort,
    HubLivenessReader,
    HubManagement,
    LeaderElector,
    LLMGateway,
    MemoryManager,
    MemoryProjector,
    MemoryRepository,
    MongoCollection,
    MongoDAL,
    RedisKV,
    RedisPubSub,
    RedisStreams,
    RoomDistributedLock,
    RoomHistoryReader,
    RoomManagement,
    RoomMembershipSeedSource,
    RoomMessageStore,
    RoomOwnershipReader,
    RoomRegistry,
    SSETransport,
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
from models.request import RoomCenterAgentMessageRequest
from room import MessageMongoRepository, RoomFacade, RoomMongoRepository
from room.membership_source import RepositoryRoomMembershipSeedSource
from room.repository import RoomQuoteMongoRepository
from room_files import LocalFileContentStore, RoomFiles

logger = get_logger(__name__)

if TYPE_CHECKING:
    from dal.runtime_store import RuntimeRepositoryStore


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
            "Running under gunicorn requires all DAL Redis services. "
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
    return RuntimeHealthCheck(
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

    from execution.orchestration.room_message_center import (
        room_message_center as execution_room_message_center,
    )

    if getattr(execution_room_message_center, "_runtime", None) is None:
        errors.append("execution.room_message_center")

    if getattr(app.state, "delivery_facade", None) is None:
        errors.append("app.state.delivery_facade")

    if getattr(app.state, "execution_deps", None) is None:
        errors.append("app.state.execution_deps")

    api_gateway_deps = getattr(app.state, "api_gateway_deps", None)
    for missing in missing_required_deps(api_gateway_deps):
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
    _bg_started = False
    agent_health_service = None
    redis_kv_ready = False
    redis_streams_ready = False

    try:
        # ── Phase 1: Infrastructure (DB + Redis, no background work) ──

        mongo_dal = create_mongo_dal()
        _mongo_dal = mongo_dal
        app.state.mongo_dal = mongo_dal
        await mongo_dal.connect()

        if await mongo_dal.ping():
            from a2a_adapter import AgentCardResolverImpl
            from a2a_adapter import artifact_storage as a2a_artifact_storage
            from a2a_adapter.remote_task_reader import RemoteTaskReader
            from agent.capability_issue import (
                CapabilityIssueExclusionReader,
            )
            from agent.health import AgentHealthService
            from agent.inspection import AgentInspectionService
            from agent.liveness import AgentLivenessService
            from agent.matcher import AgentMatcher
            from agent.resolver import (
                AgentResolverFacadeRepository,
                AgentResolverService,
            )
            from agent.route_adapter import AgentRouteAdapter
            from agent.selection_service import AgentSelectionService
            from agent.service import AgentService
            from common.utils.a2a_helpers import bind_a2a_artifact_files
            from context_memory.compat.runtime import (
                ContextMemoryChatAdapter,
                ContextMemoryRoomMemoryAdapter,
                ContextMemoryRouteCenter,
            )
            from context_memory.config import ContextMemoryLLMConfig
            from execution.orchestration.debate_prompt_injector import (
                DebatePromptInjector,
            )
            from execution.orchestration.room_supervisor_service import (
                SupervisorPlanningError,
                room_supervisor_service,
            )
            from execution.orchestration.synthesis_coordinator import (
                SynthesisCoordinator,
            )
            from llm_gateway import LLMGatewayImpl, ModelRegistryImpl
            from llm_gateway.config import LLMGatewayConfig
            from llm_gateway.services import (
                AgentSelectionLLMService,
                MessageParserLLMService,
                RoomMemoryLLMService,
                SummaryLLMService,
                SupervisorLLMService,
            )
            from room.compat.runtime import (
                build_turn_content,
                room_runtime,
                room_services,
            )
            from room.route_adapter import RoomRouteAdapter

            room_files_collection = mongo_dal.collection("room_files")
            file_storage = create_file_storage(
                room_files_collection=room_files_collection,
                rooms_collection=mongo_dal.collection("rooms"),
                room_messages_collection=mongo_dal.collection("room_user_messages"),
                room_agent_messages_collection=mongo_dal.collection(
                    "room_agent_messages"
                ),
                room_owned_collections=[
                    mongo_dal.collection(name)
                    for name in (
                        "room_user_messages",
                        "room_agent_messages",
                        "room_quotes",
                        "room_memories",
                        "conversation_content",
                        "runs",
                        "run_events",
                        "orchestration_runs",
                        "orchestration_run_events",
                        "hitl_requests",
                        "cancelled_messages",
                    )
                ],
                file_dir=runtime.settings.hybro_file_dir,
                content_url_prefix=f"{runtime.settings.api_prefix.rstrip('/')}/files",
            )

            a2a_artifact_storage.bind_artifact_files(file_storage)
            bind_a2a_artifact_files(a2a_artifact_storage)
            index_readiness = await ensure_runtime_indexes(mongo=mongo_dal)
            app.state.agent_search_index_ready = index_readiness[
                "agent_search_index_ready"
            ]
            app.state.memory_search_index_ready = index_readiness[
                "memory_search_index_ready"
            ]
            app.state.search_indexes_ready = app.state.agent_search_index_ready and (
                not runtime.settings.memory_search_enabled
                or app.state.memory_search_index_ready
            )

            route_room_center = RoomRouteAdapter()
            debate_prompt_injector = DebatePromptInjector()
            synthesis_coordinator = SynthesisCoordinator()
            remote_task_reader = RemoteTaskReader()
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
            _delivery_deps = create_delivery_deps(_delivery_facade)
            app.state.delivery_facade = _delivery_facade
            app.state.delivery_deps = _delivery_deps

            from a2a_adapter.runtime_service import (
                A2ARuntimeConfig,
                a2a_service,
            )
            from a2a_adapter.task_status import coerce_task_state
            from execution.hitl.factory import create_hitl_service

            agent_capability_issue_repository = (
                create_agent_capability_issue_repository(mongo_dal)
            )
            agent_capability_issue_service = create_agent_capability_issue_service(
                repository=agent_capability_issue_repository
            )
            from common.observability.run_metrics import increment_counter
            from delivery.task_notifier import TaskUpdateNotifier
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
            from execution.orchestration.planner import RoomSupervisorPlannerAdapter
            from execution.orchestration.resources import (
                AttachmentProjectionService,
                OrchestrationResourceProvider,
            )
            from execution.orchestration.run_store import MongoOrchestrationRunStore
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
                room_files=file_storage,
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
            summary_llm_service = SummaryLLMService(llm_provider=llm_provider)
            agent_selection_llm_service = AgentSelectionLLMService(
                llm_provider=llm_provider
            )
            message_parser_llm_service = MessageParserLLMService(
                llm_provider=llm_provider
            )
            room_memory_llm_service = RoomMemoryLLMService(llm_provider=llm_provider)
            room_supervisor_service.bind_supervisor_service(supervisor_llm_service)
            room_runtime.bind_message_parser_service(message_parser_llm_service)
            room_runtime.bind_debate_rounds(runtime.settings.debate_rounds)
            room_runtime.bind_capability_issue_reader(agent_capability_issue_service)
            synthesis_coordinator.bind_summary_service(summary_llm_service)
            agent_card_resolver = AgentCardResolverImpl()
            _agent_deps = create_agent_deps(
                mongo=mongo_dal,
                card_resolver=agent_card_resolver,
                hub_liveness=None,
                exclusion_reader=CapabilityIssueExclusionReader(
                    agent_capability_issue_service
                ),
                gateway_base_url=runtime.settings.gateway_base_url,
            )
            _agent_facade = _agent_deps.agent_registry
            agent_compat_service = AgentService(facade=_agent_facade)
            route_agent_center = AgentRouteAdapter(service=agent_compat_service)
            agent_matcher = AgentMatcher(facade=_agent_facade)
            agent_selection_service = AgentSelectionService(
                matcher=agent_matcher,
                llm_reranker=agent_selection_llm_service,
            )
            agent_resolver_service = AgentResolverService(
                repository=AgentResolverFacadeRepository(_agent_facade),
                capability_issue_reader=agent_capability_issue_service,
                agent_selection_service=agent_selection_llm_service,
            )
            agent_health_service = AgentHealthService(
                repository=_agent_deps.agent_repository
            )
            agent_liveness_checker = AgentLivenessService(
                health_service=agent_health_service,
                hub_liveness_reader=None,
                agent_registry_writer=_agent_deps.agent_registry_writer,
            )
            route_inspection_center = AgentInspectionService()
            membership_source = RepositoryRoomMembershipSeedSource(
                agent_service_adapter=agent_compat_service
            )
            _room_deps = create_room_deps(
                mongo=mongo_dal,
                agent_registry=_agent_deps.agent_registry,
                membership_source=membership_source,
                attachment_metadata_reader=file_storage,
            )
            _room_facade = _room_deps.room_registry
            # Runtime store aggregate: callers below receive focused runtime-store parts.
            # Do not add new broad-store consumers; bind a focused part or protocol instead.
            runtime_store = create_runtime_repository_store(
                mongo=mongo_dal,
                room_deps=_room_deps,
                agent_deps=_agent_deps,
            )
            agent_room_store = runtime_store.agent_room
            message_store = runtime_store.messages
            task_store = runtime_store.tasks
            hitl_store = runtime_store.hitl
            memory_store = runtime_store.memory
            max_tasks_per_user = runtime_store.MAX_TASKS_PER_USER
            max_tasks_per_room = runtime_store.MAX_TASKS_PER_ROOM

            # P3 runtime adapters keep startup wiring narrow. These SimpleNamespace
            # boundaries are intentionally constrained to container assembly.
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
                get_hitl_request=hitl_store.get_hitl_request,
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
                persist_hitl_request_id_on_message=(
                    hitl_store.persist_hitl_request_id_on_message
                ),
                find_pending_hitl_request_for_agent_message=(
                    hitl_store.find_pending_hitl_request_for_agent_message
                ),
                create_or_reuse_pending_hitl_request=(
                    hitl_store.create_or_reuse_pending_hitl_request
                ),
                persist_pending_hitl_on_agent_message=(
                    hitl_store.persist_pending_hitl_on_agent_message
                ),
                get_room_user_message_by_message_id=(
                    message_store.get_room_user_message_by_message_id
                ),
                resolve_client_request_id_for_message_id=(
                    task_store.resolve_client_request_id_for_message_id
                ),
                persist_hitl_user_answer=hitl_store.persist_hitl_user_answer,
                persist_hitl_group_metadata=hitl_store.persist_hitl_group_metadata,
                get_hitl_request=hitl_store.get_hitl_request,
                update_hitl_request=hitl_store.update_hitl_request,
                claim_hitl_request=hitl_store.claim_hitl_request,
                fenced_update_hitl_request=hitl_store.fenced_update_hitl_request,
                count_pending_in_hitl_group=hitl_store.count_pending_in_hitl_group,
                get_hitl_group_requests=hitl_store.get_hitl_group_requests,
                get_pending_hitl_group_requests_strict=(
                    hitl_store.get_pending_hitl_group_requests_strict
                ),
                get_unreconciled_terminal_hitl_group_requests_strict=(
                    hitl_store.get_unreconciled_terminal_hitl_group_requests_strict
                ),
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
                get_pending_hitl_requests_for_message_strict=(
                    hitl_store.get_pending_hitl_requests_for_message_strict
                ),
                cas_update_hitl_request=hitl_store.cas_update_hitl_request,
                cas_update_hitl_request_strict=(
                    hitl_store.cas_update_hitl_request_strict
                ),
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
                is_message_cancelled_strict=task_store.is_message_cancelled_strict,
                get_room_user_message_by_message_id=(
                    message_store.get_room_user_message_by_message_id
                ),
                get_room_user_message_by_message_id_strict=(
                    message_store.get_room_user_message_by_message_id_strict
                ),
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
                get_stale_claimed_orchestration_messages=(
                    message_store.get_stale_claimed_orchestration_messages
                ),
                update_orchestration_projection_if_status=(
                    message_store.update_orchestration_projection_if_status
                ),
                list_pending_cancellation_markers=(
                    task_store.list_pending_cancellation_markers
                ),
                mark_cancellation_reconciled=(task_store.mark_cancellation_reconciled),
                get_agent_by_agent_id=agent_room_store.get_agent_by_agent_id,
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
                get_room_user_messages_by_room_id=(
                    message_store.get_room_user_messages_by_room_id
                ),
                is_message_cancelled_strict=(task_store.is_message_cancelled_strict),
            )
            execution_message_writer = SimpleNamespace(
                accumulate_artifact_on_message=(
                    message_store.accumulate_artifact_on_message
                ),
                claim_terminal_finalization=(message_store.claim_terminal_finalization),
                begin_terminal_finalization=(message_store.begin_terminal_finalization),
                terminal_finalization_matches=(
                    message_store.terminal_finalization_matches
                ),
                claim_terminal_finalization_step=(
                    message_store.claim_terminal_finalization_step
                ),
                claim_artifact_materialization=(
                    message_store.claim_artifact_materialization
                ),
                complete_terminal_finalization=(
                    message_store.complete_terminal_finalization
                ),
                complete_terminal_finalization_step=(
                    message_store.complete_terminal_finalization_step
                ),
                heartbeat_terminal_finalization=(
                    message_store.heartbeat_terminal_finalization
                ),
                heartbeat_artifact_materialization=(
                    message_store.heartbeat_artifact_materialization
                ),
                is_artifact_update_recorded=(message_store.is_artifact_update_recorded),
                set_terminal_finalization_content=(
                    message_store.set_terminal_finalization_content
                ),
                release_artifact_materialization=(
                    message_store.release_artifact_materialization
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
                emit_synthesis_message=synthesis_coordinator.emit_synthesis_message,
            )

            async def execution_inquiry_agent_messages_by_related_message_id(
                related_message_id: str,
            ):
                request = RoomCenterAgentMessageRequest(
                    related_message_id=related_message_id
                )
                return await room_services.inquiry_agent_messages_by_related_message_id(
                    request
                )

            execution_room_runtime = SimpleNamespace(
                create_agent_message=room_services.create_agent_message,
                process_agent_message=room_services.process_agent_message,
                update_agent_message_by_message_id=(
                    room_services.update_agent_message_by_message_id
                ),
                inquiry_agent_messages_by_related_message_id=(
                    execution_inquiry_agent_messages_by_related_message_id
                ),
            )
            execution_delivery = _delivery_facade
            task_notifier = TaskUpdateNotifier(execution_delivery)
            execution_a2a_transport = SimpleNamespace(
                has_streaming_capability=a2a_service.has_streaming_capability,
                send_message_streaming=a2a_service.send_message_streaming,
                send_message_sync=a2a_service.send_message_sync,
                send_message_to_tracked_agent=(
                    a2a_service.send_message_to_tracked_agent
                ),
                create_task_for_tracking=a2a_service.create_task_for_tracking,
                cancel_remote_task=a2a_service.cancel_remote_task,
                has_push_notification_capability=(
                    a2a_service.has_push_notification_capability
                ),
            )
            execution_remote_task_reader = remote_task_reader

            membership_source.bind_store(agent_room_store)
            debate_prompt_injector.bind_store(debate_message_store)
            synthesis_coordinator.bind_store(room_coordinator_message_store)
            synthesis_coordinator.bind_delivery(execution_delivery)
            bind_notification_store(task_notification_store)
            bind_task_notification_runtime(
                task_notifier=task_notifier,
                delivery=execution_delivery,
            )
            a2a_service.bind_runtime_config(
                A2ARuntimeConfig(webhook_base_url=runtime.settings.webhook_base_url)
            )
            a2a_service.bind_task_db(
                a2a_task_tracking_store,
                call_counter=agent_room_store,
            )
            execution_client_request_id_resolver = SSEClientRequestIdResolver(
                resolver=task_store,
            )
            app.state.execution_client_request_id_resolver = (
                execution_client_request_id_resolver
            )
            orchestration_run_store = MongoOrchestrationRunStore(
                mongo_dal,
                room_files=file_storage,
            )
            hitl_manager = create_hitl_service(
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
                room_files=file_storage,
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
            room_runtime.bind_store(room_runtime_store)
            room_runtime.bind_legacy_dependencies(
                agent_service=agent_compat_service,
                agent_selection_service=agent_selection_service,
                a2a_service=a2a_service,
                delivery=execution_delivery,
                remote_task_reader=remote_task_reader,
            )
            room_runtime.bind_facade(_room_facade)
            room_runtime.bind_message_event_publisher(_delivery_deps.event_publisher)
            room_runtime.bind_room_files(file_storage)
            room_runtime.bind_attachment_metadata_reader(file_storage)
            room_runtime.bind_attachment_content_reader(file_storage)
            room_runtime.bind_a2a_inline_file_limits(
                max_raw_bytes=runtime.settings.a2a_inline_file_max_raw_bytes,
                max_encoded_bytes=runtime.settings.a2a_inline_message_max_encoded_bytes,
            )
            route_room_center.bind_facade(_room_facade)
            context_memory_facade = create_context_memory_facade(
                mongo=mongo_dal,
                llm_provider=llm_provider,
                room_history_reader=_room_deps.room_history_reader,
                search_config=MemorySearchConfig(
                    enabled=(
                        runtime.settings.memory_search_enabled
                        and app.state.memory_search_index_ready
                    ),
                    temporal_decay_enabled=(
                        runtime.settings.memory_search_temporal_decay_enabled
                    ),
                    half_life_days=runtime.settings.memory_search_half_life_days,
                    max_results=runtime.settings.memory_search_max_results,
                    max_candidates=runtime.settings.memory_search_max_candidates,
                    max_snippet_chars=runtime.settings.memory_search_max_snippet_chars,
                ),
                llm_config=ContextMemoryLLMConfig(
                    turn_notes_model="context_memory_legacy_json_model",
                    summary_model="context_memory_legacy_json_model",
                ),
            )
            _context_memory_deps = create_context_memory_deps(context_memory_facade)
            context_memory_chat_adapter = ContextMemoryChatAdapter(
                chat_store=memory_store,
                chat_context_llm=room_memory_llm_service,
            )
            route_memory_center = ContextMemoryRouteCenter(
                chat_adapter=context_memory_chat_adapter,
            )
            context_memory_room_memory = ContextMemoryRoomMemoryAdapter(
                facade=context_memory_facade,
                usage_store=memory_store,
            )
            execution_room_memory = SimpleNamespace(
                add_synthesis_to_history=(
                    context_memory_room_memory.add_synthesis_to_history
                ),
                update_room_summary=context_memory_room_memory.update_room_summary,
            )
            agent_rate_limiter = None
            if getattr(app.state, "agent_rate_limiter_factory", None):
                agent_rate_limiter = app.state.agent_rate_limiter_factory(
                    runtime.settings, mongo_dal
                )
            orchestration_planner = RoomSupervisorPlannerAdapter(
                supervisor_service=room_supervisor_service
            )
            orchestration_resource_provider = OrchestrationResourceProvider(
                projection_service=AttachmentProjectionService(
                    content_reader=file_storage,
                )
            )

            room_message_center_impl = create_room_message_center(
                room_runtime=execution_room_runtime,
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
                delivery=execution_delivery,
                event_publisher=_delivery_deps.event_publisher,
                coordinator=execution_coordinator,
                summary_service=summary_llm_service,
                task_notifier=task_notifier,
                task_notification_store=task_notification_store,
                agent_resolver_service=agent_resolver_service,
                a2a_transport=execution_a2a_transport,
                remote_task_reader=execution_remote_task_reader,
                room_memory=execution_room_memory,
                debate_prompt_injector=debate_prompt_injector,
                rate_limit_service=agent_rate_limiter,
                room_supervisor_service=room_supervisor_service,
                orchestration_run_store=orchestration_run_store,
                orchestration_planner=orchestration_planner,
                orchestration_resource_provider=orchestration_resource_provider,
                guardrails_enabled=runtime.settings.orchestration_outcome_guardrails,
                hitl_coordinator=hitl_manager,
                task_notifications=TaskNotificationAdapter(
                    notify_task_update_with_string_state
                ),
                task_notification_impl=_notify_task_update_impl,
                agent_health_service=agent_health_service,
                room_files=file_storage,
                capability_issue_service=agent_capability_issue_service,
                context_memory_runtime=_context_memory_deps.context_memory_runtime,
                context_compaction=context_memory_facade,
                build_turn_content_func=build_turn_content,
                supervisor_planning_error_cls=SupervisorPlanningError,
                orphan_threshold_minutes=runtime.settings.orphan_threshold_minutes,
                debate_rounds=runtime.settings.debate_rounds,
                cloud_health_cache_ttl=runtime.settings.cloud_health_cache_ttl,
                cloud_health_check_timeout=runtime.settings.cloud_health_check_timeout,
            )
            execution_room_message_center.bind(room_message_center_impl)
            execution_room_message_center.bind_facade(_room_facade)
            stale_task_checker.set_terminal_event_handler(
                execution_room_message_center.agent_response_handler.handle
            )

            def create_webhook_transport():
                async def fetch_terminal_webhook_task(agent_url: str, task_id: str):
                    agent_card = await a2a_service.get_agent_card_from_url(agent_url)
                    return await remote_task_reader.get_task_from_agent(
                        agent_card, task_id
                    )

                handler = AgentResponseHandler(
                    message_writer=message_store,
                    task_writer=message_store,
                    continuation_store=task_store,
                    client_request_resolver=response_client_request_resolver,
                    room_reader=agent_room_store,
                    hitl_reader=hitl_store,
                    delivery=execution_delivery,
                    room_message_center=execution_room_message_center,
                    hitl_coordinator=hitl_manager,
                    task_notifier=task_notifier,
                    task_notification_store=task_notification_store,
                    task_notification_impl=_notify_task_update_impl,
                    room_files=file_storage,
                )
                handler.bind_execution_event_deps(emit_room_processing_status)
                return WebhookTransport(
                    response_handler=handler,
                    webhook_auth=task_store,
                    message_reader=message_store,
                    cancellation_reader=task_store,
                    task_notifier=notify_task_update_with_string_state,
                    terminal_task_fetcher=fetch_terminal_webhook_task,
                )

            execution_facade = create_execution_facade(
                room_center=route_room_center,
                room_message_center=execution_room_message_center,
                hitl_manager=hitl_manager,
                run_lifecycle=run_lifecycle,
                run_reader=RunQueryAdapter(_execution_repos["run_repository"]),
                cancellation_state=CancellationStateC3Adapter(execution_delivery),
                cancellation_store=MongoCancellationStoreAdapter(task_store),
                hitl_message_cancellation=HITLMessageCancellationAdapter(hitl_manager),
                agent_task_cleanup=AgentTaskCleanupAdapter(
                    message_task_store=message_store,
                    get_agent_card_from_url=a2a_service.get_agent_card_from_url,
                    cancel_remote_task=a2a_service.cancel_remote_task,
                    notify_task_update=notify_task_update_with_string_state,
                ),
                agent_response_handler=execution_room_message_center.agent_response_handler,
                event_publisher=_delivery_deps.event_publisher,
                run_event_enabled=run_event_sse_enabled,
                client_request_id_resolver=execution_client_request_id_resolver,
                orchestration_run_store=orchestration_run_store,
            )
            _execution_deps = create_execution_deps(execution_facade)

            async def emit_room_processing_status(**kwargs):
                return await emit_execution_room_processing_status(
                    **kwargs,
                    run_lifecycle=run_lifecycle,
                    event_publisher=_delivery_deps.event_publisher,
                    run_event_enabled=run_event_sse_enabled,
                    client_request_id_resolver=execution_client_request_id_resolver,
                )

            bind_task_processing_status_emitter(emit_room_processing_status)
            execution_room_message_center.bind_execution_event_deps(
                emit_room_processing_status
            )
            app.state.execution_facade = execution_facade
            app.state.execution_deps = _execution_deps

            register_context_memory_event_handlers(
                event_publisher=_delivery_deps.event_publisher,
                context_memory_facade=context_memory_facade,
            )
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
            logger.warning("startup heal: failed; continuing startup", exc_info=True)
        else:
            if healed:
                logger.info("startup heal: healed %s diverged run(s)", healed)
        await hitl_store.ensure_hitl_indexes()

        # Init DAL Redis subsystems before the guard. Delivery-owned
        # Pub/Sub/KV clients are constructed through container.py above.
        _redis_runtime = create_redis_runtime_deps(
            redis_url=runtime.settings.redis_url,
            instance_id=(
                _delivery_facade.instance_id if _delivery_facade is not None else None
            ),
            relay_stream_maxlen=runtime.settings.relay_stream_maxlen,
            relay_hub_heartbeat_ttl=runtime.settings.relay_hub_heartbeat_ttl,
        )
        _redis_service = _redis_runtime.command_client
        if _redis_service:
            redis_kv_ready = await _redis_service.ping()
            if redis_kv_ready:
                logger.info("DAL Redis KV connected (leader election/relay enabled)")
            else:
                logger.warning("DAL Redis KV unavailable; Redis KV features disabled")
        else:
            logger.info("DAL Redis disabled (REDIS_URL not set)")
        app.state.redis_runtime = _redis_runtime

        _redis_streams_service = _redis_runtime.streams_client
        if _redis_streams_service:
            redis_streams_ready = await _redis_streams_service.ping()
            if redis_streams_ready:
                logger.info("DAL Redis Streams connected")
            else:
                logger.warning("DAL Redis Streams unavailable; relay streams disabled")

        # ── Guard: fail if gunicorn without fully connected Redis ──
        check_multi_worker_safety(
            is_gunicorn=runtime.settings.is_gunicorn,
            delivery_pubsub_connected=bool(
                _delivery_facade and _delivery_facade.delivery_pubsub_connected
            ),
            delivery_kv_connected=bool(
                _delivery_facade and _delivery_facade.delivery_kv_connected
            ),
            redis_service_connected=redis_kv_ready,
            relay_streams_connected=redis_streams_ready,
            change_stream_connected=bool(
                _delivery_facade and _delivery_facade.change_stream_connected
            ),
        )

        # ── Phase 2: Background services (only after guard passes) ──

        if redis_kv_ready:
            _leader = _redis_runtime.leader
            logger.info("Leader election enabled for background jobs")

        if agent_health_service is None:
            raise RuntimeError("Agent health service has not been initialized")
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
                StaleCancellationFinalizerDeps,
                StaleHITLDeps,
                StaleOrchestrationRunRecoveryDeps,
                StaleRecoveryDeps,
                StaleRunWatchdogEventDeps,
            )

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
                    client_request_id_resolver=execution_client_request_id_resolver,
                )

            stale_task_checker.set_execution_recovery_deps(
                StaleRecoveryDeps(
                    schedule_recovery=execution_facade.schedule_recovery_orchestration,
                )
            )
            stale_task_checker.set_orchestration_run_recovery_deps(
                StaleOrchestrationRunRecoveryDeps(
                    orchestration_run_store=orchestration_run_store,
                )
            )
            stale_task_checker.set_cancellation_finalizer_deps(
                StaleCancellationFinalizerDeps(
                    finalize=execution_facade.finalize_pending_cancellation,
                )
            )
            stale_task_checker.set_hitl_deps(
                StaleHITLDeps(
                    recover_stale_processing=hitl_manager.recover_stale_processing,
                    cancel_requests_for_message=hitl_manager.cancel_requests_for_message,
                )
            )
            stale_task_checker.set_run_watchdog_event_deps(
                StaleRunWatchdogEventDeps(
                    append_run_timeout_failure=run_lifecycle.append_run_timeout_failure,
                    emit_run_event=emit_watchdog_run_event,
                    emit_processing_status=emit_watchdog_processing_status,
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
                context_compaction=context_memory_facade,
            )
        )
        orphaned_upload_cleaner.set_leader_election(_leader)
        orphaned_upload_cleaner.set_cleanup_deps(
            OrphanedUploadCleanerDeps(
                room_files=file_storage,
            )
        )

        _bg_started = True
        await agent_health_service.start()

        await stale_task_checker.start()
        await stale_task_checker.check_stale_tasks()
        if runtime.settings.webhook_signing_key:
            logger.info(
                "A2A push-notification support initialized (using room_agent_messages)"
            )
        else:
            logger.warning(
                "WEBHOOK_SIGNING_KEY not set - A2A push notifications disabled; "
                "durable orchestration recovery remains enabled"
            )

        await compaction_sweep.start()
        await orphaned_upload_cleaner.start()

        # Initialize relay service
        from execution.facade import hub_agent_response_internal_to_agent_event
        from hub_runtime_bridge.adapters.legacy_failure import (
            RelayOfflineFailureAdapter,
        )
        from hub_runtime_bridge.adapters.relay_hub_store import RelayHubStore
        from hub_runtime_bridge.compat.relay_service import (
            RelayHubLivenessReader,
            init_relay_service,
        )
        from hub_runtime_bridge.config import config_from_settings
        from hub_runtime_bridge.repository.mongo import HubMongoRepository

        _rmc = execution_room_message_center
        bind_redis_runtime_to_room(
            _rmc,
            redis_runtime=_redis_runtime,
            redis_kv_ready=redis_kv_ready,
        )
        relay_hub_store = RelayHubStore(
            mongo=mongo_dal,
            hub_repository=HubMongoRepository(mongo_dal),
            agent_repository=_agent_deps.agent_repository,
        )
        _relay_svc = init_relay_service(
            mongo=relay_hub_store,
            db=relay_runtime_store,
            room_message_center=_rmc,
            hitl_coordinator=hitl_manager,
            event_publisher=_delivery_deps.event_publisher if _delivery_deps else None,
            worker_id=(
                _delivery_facade.instance_id if _delivery_facade is not None else None
            ),
            response_converter=hub_agent_response_internal_to_agent_event,
            offline_failure_port=RelayOfflineFailureAdapter(
                message_store,
                _delivery_facade,
            ),
            config=config_from_settings(settings),
        )
        app.state.relay_service = _relay_svc
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
            agent_liveness_checker.bind_deps(
                health_service=agent_health_service,
                hub_liveness_reader=hub_liveness_reader,
                agent_registry_writer=_agent_deps.agent_registry_writer,
            )
        await _relay_svc.start()
        logger.info("Relay service initialized and heartbeat checker started")

        # Attach Redis Streams to relay service
        if bind_redis_runtime_to_relay(
            _relay_svc,
            redis_runtime=_redis_runtime,
            redis_streams_ready=redis_streams_ready,
        ):
            logger.info(
                "Redis Streams relay enabled (separate pool for blocking XREAD)"
            )

        bind_api_gateway_deps(
            app,
            APIGatewayDeps(
                task_store=a2a_task_status_reader,
                agent_center=route_agent_center,
                agent_service=_agent_deps.agent_registry,
                capability_issue_service=agent_capability_issue_service,
                agent_liveness_checker=agent_liveness_checker,
                agent_group_store=agent_room_store,
                api_key_store=None,
                discovery_service=None,
                discovery_rate_limiter=None,
                discovery_default_limit=runtime.settings.discovery_default_limit,
                file_storage=file_storage,
                room_ownership_reader=_room_deps.room_registry,
                hitl_manager=_execution_deps.hitl_manager,
                hub_relay_service=_relay_svc,
                inspection_center=route_inspection_center,
                memory_center=route_memory_center,
                gateway_service=None,
                gateway_rate_limiter=None,
                relay_service=_relay_svc,
                room_center=route_room_center,
                room_store=route_room_reader,
                agent_selection_service=agent_selection_service,
                execution_engine=_execution_deps.execution_engine,
                sse_store=sse_state_reader,
                sse_transport=_delivery_facade,
                webhook_receiver=create_webhook_transport(),
                repository_provider=DALViewSetRepositoryProvider(mongo=mongo_dal),
            ),
        )

    except BaseException:
        # ── Startup failure: tear down only what was opened ──
        # Do not call set_draining() on startup failure; normal shutdown owns
        # the drain window after the adapter has been successfully bound.
        if _relay_svc:
            await _relay_svc.stop()
        if _bg_started:
            await stale_task_checker.stop()
            await compaction_sweep.stop()
            await orphaned_upload_cleaner.stop()
            if agent_health_service is not None:
                await agent_health_service.stop()
        if _leader:
            await _leader.release_all(ALL_JOB_NAMES)
        await close_redis_runtime_deps(_redis_runtime)
        if _mongo_dal is not None:
            await _mongo_dal.close()
            app.state.mongo_dal = None
        try:
            if _delivery_facade is not None:
                await _delivery_facade.stop()
        finally:
            app.state.delivery_facade = None
        raise

    # ── Phase 3: Serve + Normal Shutdown ──
    try:
        yield
    finally:
        # Stop the relay service heartbeat checker
        from hub_runtime_bridge.compat.relay_service import (
            relay_service as _relay_svc_shutdown,
        )

        if _relay_svc_shutdown:
            await _relay_svc_shutdown.stop()

        # Stop background services
        await stale_task_checker.stop()
        await compaction_sweep.stop()
        await orphaned_upload_cleaner.stop()
        if agent_health_service is not None:
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
        if _delivery_facade is not None:
            _delivery_facade.set_draining(True)
        await asyncio.sleep(
            _delivery_config.shutdown_drain_seconds
            if _delivery_config is not None
            else runtime.settings.shutdown_drain_seconds
        )

        try:
            if _delivery_facade is not None:
                await _delivery_facade.stop()
        finally:
            app.state.delivery_facade = None

        await close_redis_runtime_deps(_redis_runtime)
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


@dataclass(frozen=True)
class RedisRuntimeDeps:
    command_client: RedisKV | None
    streams_client: RedisStreams | None
    leader: LeaderElector | None
    room_lock: RoomDistributedLock | None
    relay_streams: Any | None


def create_redis_runtime_deps(
    *,
    redis_url: str,
    instance_id: str | None = None,
    relay_stream_maxlen: int | None = None,
    relay_hub_heartbeat_ttl: int | None = None,
) -> RedisRuntimeDeps:
    if not redis_url:
        return RedisRuntimeDeps(
            command_client=None,
            streams_client=None,
            leader=None,
            room_lock=None,
            relay_streams=None,
        )

    from dal.redis.kv import RedisKVImpl
    from dal.redis.lock import LeaderElectorImpl, RoomRedisDistributedLock
    from dal.redis.streams import RedisStreamsImpl
    from hub_runtime_bridge.transport.relay_streams import RelayStreamService

    command_client = RedisKVImpl(url=redis_url)
    shared_command_client = command_client._ensure_client()
    streams_client = RedisStreamsImpl(url=redis_url)
    return RedisRuntimeDeps(
        command_client=command_client,
        streams_client=streams_client,
        leader=(
            LeaderElectorImpl(client=shared_command_client, instance_id=instance_id)
            if instance_id is not None
            else None
        ),
        room_lock=RoomRedisDistributedLock(client=shared_command_client),
        relay_streams=RelayStreamService(
            streams_client,
            kv=command_client,
            maxlen=relay_stream_maxlen or settings.relay_stream_maxlen,
            heartbeat_ttl=(relay_hub_heartbeat_ttl or settings.relay_hub_heartbeat_ttl),
        ),
    )


async def close_redis_runtime_deps(redis_runtime: RedisRuntimeDeps | None) -> None:
    if redis_runtime is None:
        return

    closed: set[int] = set()
    for attr in ("streams_client", "command_client", "leader", "room_lock"):
        client = getattr(redis_runtime, attr, None)
        close = getattr(client, "close", None)
        close_target = getattr(client, "_client", None) or client
        if close is None or id(close_target) in closed:
            continue
        closed.add(id(close_target))
        await close()


def bind_redis_runtime_to_room(
    room_message_center: Any,
    *,
    redis_runtime: RedisRuntimeDeps,
    redis_kv_ready: bool,
) -> None:
    room_message_center.set_room_distributed_lock(
        redis_runtime.room_lock if redis_kv_ready else None
    )


def bind_redis_runtime_to_relay(
    relay_service: Any,
    *,
    redis_runtime: RedisRuntimeDeps,
    redis_streams_ready: bool,
) -> bool:
    relay_streams = redis_runtime.relay_streams
    if redis_streams_ready and relay_streams:
        relay_service.set_stream_service(relay_streams)
        return True
    return False


def create_mongo_dal() -> MongoDAL:
    from dal.mongo import MongoDALImpl

    return MongoDALImpl()


async def ensure_runtime_indexes(*, mongo: MongoDAL) -> dict[str, bool]:
    agent_search_index_ready = await _ensure_agent_indexes(mongo)
    memory_search_index_ready = await _ensure_context_memory_indexes(mongo)
    await _ensure_capability_issue_indexes(mongo)
    await _ensure_run_lifecycle_indexes(mongo)
    await _ensure_orchestration_run_indexes(mongo)
    await _ensure_room_quote_indexes(mongo)
    await _ensure_user_message_indexes(mongo)
    await _ensure_task_tracking_indexes(mongo)
    await _ensure_cancellation_indexes(mongo)
    await _ensure_room_file_indexes(mongo)
    return {
        "agent_search_index_ready": agent_search_index_ready,
        "memory_search_index_ready": memory_search_index_ready,
    }


async def _ensure_agent_indexes(mongo: MongoDAL) -> bool:
    agents = mongo.collection("agents")
    existing = await agents.index_information()
    index = existing.get("unique_normalized_url")
    needs_recreate = index is None or index.get("partialFilterExpression") != {
        "normalized_url": {"$type": "string"}
    }
    if needs_recreate:
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
    return await _ensure_text_index(
        agents,
        name="agent_lexical_text",
        weights={
            "agent_card.name": 10,
            "agent_card.skills.name": 8,
            "agent_card.skills.tags": 6,
            "agent_card.description": 3,
            "agent_card.skills.description": 3,
        },
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
) -> bool:
    collection = mongo.collection(collection_name)
    try:
        await collection.create_index(keys, unique=unique, name=name, **kwargs)
        return True
    except Exception as exc:
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
        return False


async def _ensure_context_memory_indexes(mongo: MongoDAL) -> bool:
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
    memory_search_ready = await _ensure_text_index(
        mongo.collection("conversation_content"),
        name="turn_notes_text",
        weights={
            "content": 1,
            "turn_notes.keywords": 1,
            "turn_notes.entities": 1,
            "turn_notes.tags": 1,
            "turn_notes.one_liner": 1,
        },
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
    return memory_search_ready


async def _ensure_text_index(
    collection: MongoCollection,
    *,
    name: str,
    weights: dict[str, int],
) -> bool:
    log = get_logger(__name__)
    try:
        existing = await collection.index_information()
        desired_weights = dict(sorted(weights.items()))
        matching = existing.get(name)
        matching_keys = tuple(
            (key, direction) for key, direction in ((matching or {}).get("key") or [])
        )
        valid_text_keys = {
            (("_fts", "text"), ("_ftsx", 1)),
            tuple((field, "text") for field in weights),
        }
        if (
            matching
            and dict(sorted((matching.get("weights") or {}).items())) == desired_weights
            and matching_keys in valid_text_keys
        ):
            return True
        for index_name, spec in existing.items():
            keys = spec.get("key") or []
            if spec.get("weights") or any(key == "_fts" for key, _ in keys):
                await collection.drop_index(index_name)
        await collection.create_index(
            [(field, "text") for field in weights],
            name=name,
            unique=False,
            weights=weights,
        )
        return True
    except Exception:
        log.warning("Search index creation failed for %s", name, exc_info=True)
        return False


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
        [("run_id", 1), ("type", 1), ("causation_id", 1)],
        name="run_type_causation_unique",
        unique=True,
        partialFilterExpression={"causation_id": {"$type": "string"}},
    )
    await _create_index(
        mongo,
        "run_events",
        [("room_id", 1), ("ts", -1)],
        name="room_ts",
    )


async def _ensure_orchestration_run_indexes(mongo: MongoDAL) -> None:
    await _create_index(
        mongo,
        "orchestration_runs",
        [("run_id", 1)],
        name="orchestration_run_id_unique",
        unique=True,
        critical=True,
    )
    await _create_index(
        mongo,
        "orchestration_runs",
        [("user_message_id", 1), ("created_at", -1)],
        name="orchestration_user_message_created_at",
    )
    await _create_index(
        mongo,
        "orchestration_runs",
        [("status", 1), ("updated_at", 1)],
        name="orchestration_status_updated_at",
    )
    await _create_index(
        mongo,
        "orchestration_run_events",
        [("event_id", 1)],
        name="orchestration_event_id_unique",
        unique=True,
        critical=True,
    )
    await _create_index(
        mongo,
        "orchestration_run_events",
        [("run_id", 1), ("created_at", 1)],
        name="orchestration_run_created_at",
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


async def _ensure_user_message_indexes(mongo: MongoDAL) -> None:
    collection = mongo.collection("room_user_messages")
    issues = await _user_message_index_readiness_issues(collection)
    if issues:
        details = "; ".join(issues)
        logger.error(
            "room_user_messages unique-index readiness failed: %s",
            details,
        )
        raise RuntimeError(
            "room_user_messages cannot enable correctness-critical unique indexes: "
            f"{details}. Repair the historical rows explicitly; startup did not "
            "delete or merge any messages."
        )

    await _create_index(
        mongo,
        "room_user_messages",
        [("message_id", 1)],
        name="room_user_message_id_unique",
        unique=True,
        critical=True,
    )
    await _create_index(
        mongo,
        "room_user_messages",
        [("room_id", 1), ("client_request_id", 1)],
        name="room_user_client_request_id_unique",
        unique=True,
        critical=True,
        partialFilterExpression={
            "room_id": {"$type": "string"},
            "client_request_id": {"$type": "string"},
        },
    )


async def _user_message_index_readiness_issues(
    collection: MongoCollection,
    *,
    sample_limit: int = 5,
) -> list[str]:
    """Audit historical rows server-side before enabling unique constraints."""

    string_message_id = {"$eq": [{"$type": "$message_id"}, "string"]}
    trimmed_message_id = {
        "$trim": {"input": {"$cond": [string_message_id, "$message_id", ""]}}
    }
    string_room_id = {"$eq": [{"$type": "$room_id"}, "string"]}
    trimmed_room_id = {"$trim": {"input": {"$cond": [string_room_id, "$room_id", ""]}}}
    string_client_request_id = {"$eq": [{"$type": "$client_request_id"}, "string"]}
    normalized_client_request_input = {
        "$cond": [
            string_client_request_id,
            "$client_request_id",
            "",
        ]
    }
    trimmed_client_request_id = {"$trim": {"input": normalized_client_request_input}}
    client_request_id_length = {"$strLenCP": normalized_client_request_input}

    checks: list[tuple[str, list[dict[str, Any]]]] = [
        (
            "duplicate non-empty message_id",
            [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                string_message_id,
                                {"$ne": [trimmed_message_id, ""]},
                            ]
                        }
                    }
                },
                {"$group": {"_id": "$message_id", "occurrences": {"$sum": 1}}},
                {"$match": {"occurrences": {"$gt": 1}}},
                {
                    "$project": {
                        "_id": 0,
                        "message_id": "$_id",
                        "occurrences": 1,
                    }
                },
                {"$limit": sample_limit},
            ],
        ),
        (
            "missing, null, non-string, or empty message_id",
            [
                {
                    "$match": {
                        "$expr": {
                            "$or": [
                                {"$ne": [{"$type": "$message_id"}, "string"]},
                                {"$eq": [trimmed_message_id, ""]},
                            ]
                        }
                    }
                },
                {"$project": {"_id": 1, "message_id": 1}},
                {"$limit": sample_limit},
            ],
        ),
        (
            "duplicate (room_id, normalized client_request_id)",
            [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                string_room_id,
                                string_client_request_id,
                                {"$ne": [trimmed_room_id, ""]},
                                {"$ne": [trimmed_client_request_id, ""]},
                            ]
                        }
                    }
                },
                {
                    "$project": {
                        "room_id": 1,
                        "client_request_id": trimmed_client_request_id,
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "room_id": "$room_id",
                            "client_request_id": "$client_request_id",
                        },
                        "occurrences": {"$sum": 1},
                    }
                },
                {"$match": {"occurrences": {"$gt": 1}}},
                {
                    "$project": {
                        "_id": 0,
                        "room_id": "$_id.room_id",
                        "client_request_id": "$_id.client_request_id",
                        "occurrences": 1,
                    }
                },
                {"$limit": sample_limit},
            ],
        ),
        (
            "invalid or non-normalized client_request_id",
            [
                {
                    "$match": {
                        "$expr": {
                            "$or": [
                                {
                                    "$and": [
                                        string_client_request_id,
                                        {
                                            "$or": [
                                                {
                                                    "$eq": [
                                                        trimmed_client_request_id,
                                                        "",
                                                    ]
                                                },
                                                {
                                                    "$ne": [
                                                        trimmed_client_request_id,
                                                        "$client_request_id",
                                                    ]
                                                },
                                                {
                                                    "$gt": [
                                                        client_request_id_length,
                                                        MAX_CLIENT_REQUEST_ID_LENGTH,
                                                    ]
                                                },
                                            ]
                                        },
                                    ]
                                },
                                {
                                    "$not": [
                                        {
                                            "$in": [
                                                {"$type": "$client_request_id"},
                                                ["missing", "null", "string"],
                                            ]
                                        }
                                    ]
                                },
                            ]
                        }
                    }
                },
                {
                    "$project": {
                        "_id": 1,
                        "room_id": 1,
                        "client_request_id": 1,
                    }
                },
                {"$limit": sample_limit},
            ],
        ),
        (
            "missing, null, non-string, or empty room_id",
            [
                {
                    "$match": {
                        "$expr": {
                            "$or": [
                                {"$ne": [{"$type": "$room_id"}, "string"]},
                                {"$eq": [trimmed_room_id, ""]},
                            ]
                        }
                    }
                },
                {"$project": {"_id": 1, "room_id": 1, "message_id": 1}},
                {"$limit": sample_limit},
            ],
        ),
    ]

    issues: list[str] = []
    for label, pipeline in checks:
        samples = await collection.aggregate(pipeline)
        if samples:
            issues.append(
                f"{label}: found at least {len(samples)}; samples={samples!r}"
            )
    return issues


async def _ensure_task_tracking_indexes(mongo: MongoDAL) -> None:
    await _create_index(
        mongo,
        "room_agent_messages",
        [("message_id", 1)],
        name="room_agent_message_id_unique",
        unique=True,
        critical=True,
    )
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


async def _ensure_cancellation_indexes(mongo: MongoDAL) -> None:
    await _create_index(
        mongo,
        "cancelled_messages",
        [("reconciliation_status", 1), ("message_id", 1)],
        name="cancellation_reconciliation_message",
    )


async def _ensure_room_file_indexes(mongo: MongoDAL) -> None:
    await _create_index(
        mongo,
        "room_files",
        [("file_id", 1)],
        name="room_file_id_unique",
        unique=True,
        critical=True,
    )
    await _create_index(
        mongo,
        "room_files",
        [("room_id", 1), ("created_at", -1)],
        name="room_file_room_created",
    )
    await _create_index(
        mongo,
        "room_files",
        [("source_message_id", 1)],
        name="room_file_source_message",
        sparse=True,
    )
    await _create_index(
        mongo,
        "room_files",
        [("origin_key", 1)],
        name="room_file_origin_unique",
        unique=True,
        partialFilterExpression={"origin_key": {"$type": "string"}},
    )
    await _create_index(
        mongo,
        "room_files",
        [("status", 1), ("updated_at", 1)],
        name="room_file_status_updated",
    )
    await _create_index(
        mongo,
        "room_files",
        [
            ("source", 1),
            ("status", 1),
            ("last_referenced_at", 1),
            ("created_at", 1),
        ],
        name="room_file_retention",
    )
    await _create_index(
        mongo,
        "room_files",
        [("reference_claims.message_id", 1)],
        name="room_file_reference_message",
    )


def create_agent_capability_issue_repository(mongo: MongoDAL) -> Any:
    from agent.repository.capability_issue_mongo import (
        AgentCapabilityIssueMongoRepository,
    )

    return AgentCapabilityIssueMongoRepository(mongo=mongo)


def create_agent_capability_issue_service(*, repository: Any) -> Any:
    from agent.capability_issue import AgentCapabilityIssueService

    return AgentCapabilityIssueService(repository=repository)


def create_file_storage(
    *,
    room_files_collection: MongoCollection,
    rooms_collection: MongoCollection,
    room_messages_collection: MongoCollection,
    room_agent_messages_collection: MongoCollection,
    room_owned_collections: list[MongoCollection],
    file_dir: str = "",
    max_upload_bytes: int = 5 * 1024 * 1024,
    content_url_prefix: str = "/api/v1/files",
) -> FileStorage:
    from platformdirs import user_data_path

    root = file_dir or str(user_data_path("hybro", appauthor=False) / "files")
    return RoomFiles(
        metadata=room_files_collection,
        content=LocalFileContentStore(root),
        rooms=rooms_collection,
        messages=room_messages_collection,
        agent_messages=room_agent_messages_collection,
        room_owned_collections=room_owned_collections,
        lease_writes=True,
        max_upload_bytes=max_upload_bytes,
        content_url_prefix=content_url_prefix,
    )


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
    resolved_instance_id = instance_id or (
        get_instance_id() if id_factory is None else resolved_id_factory()
    )
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
    card_resolver: AgentCardResolver,
    hub_liveness: HubLivenessReader | None = None,
    exclusion_reader: AgentExclusionReader | None = None,
    gateway_base_url: str | None = None,
) -> AgentDeps:
    repository = AgentMongoRepository(mongo=mongo)
    facade = AgentFacade(
        repository=repository,
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
        agent_call_counter=facade,
        agent_repository=repository,
    )


def create_room_deps(
    *,
    mongo: MongoDAL,
    agent_registry: AgentRegistry,
    membership_source: RoomMembershipSeedSource,
    attachment_metadata_reader: AttachmentMetadataReader | None = None,
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
        attachment_metadata_reader=attachment_metadata_reader,
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
        temporal_decay_enabled=settings.memory_search_temporal_decay_enabled,
        half_life_days=settings.memory_search_half_life_days,
        max_results=settings.memory_search_max_results,
        max_candidates=settings.memory_search_max_candidates,
        max_snippet_chars=settings.memory_search_max_snippet_chars,
    )
    return ContextMemoryFacade(
        memory_repository=memory_repository,
        content_repository=content_repository,
        room_history_reader=room_history_reader,
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


def register_context_memory_event_handlers(
    *,
    event_publisher: EventPublisher,
    context_memory_facade: ContextMemoryFacade,
):
    from context_memory.events import ContextMemoryEventHandler

    handler = ContextMemoryEventHandler(
        projector=context_memory_facade,
        project_for_event=context_memory_facade.project_message_for_event,
    )
    event_publisher.register_internal_handler(
        "message_committed",
        handler.handle_message_committed,
    )
    return handler


def create_runtime_repository_store(
    *,
    mongo: MongoDAL,
    room_deps: RoomDeps,
    agent_deps: AgentDeps,
) -> RuntimeRepositoryStore:
    from dal.runtime_store import RuntimeRepositoryStore

    return RuntimeRepositoryStore(
        mongo=mongo,
        room_repository=room_deps.room_repository,
        message_repository=room_deps.message_repository,
        agent_repository=agent_deps.agent_repository,
    )
