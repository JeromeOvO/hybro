import asyncio
import importlib
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from uvicorn.config import LOGGING_CONFIG

import api_gateway
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
from app_shell.health_check import AppShellHealthCheck, HealthCheck
from app_shell.viewset import AppShellViewSetRepositoryProvider
from common.api_key_auth import bind_api_key_authenticator
from common.auth import bind_auth_config
from common.config.settings import settings
from common.middleware.discovery_cors_middleware import DiscoveryCORSMiddleware
from jobs.cleanup_orphaned_uploads import (
    OrphanedUploadCleanerDeps,
    orphaned_upload_cleaner,
)
from jobs.compaction_sweep import CompactionSweepDeps, compaction_sweep
from jobs.constants import ALL_JOB_NAMES
from jobs.stale_task_checker import StaleTaskCheckerDeps, stale_task_checker

load_dotenv()
bind_auth_config(
    clerk_secret_key_value=settings.clerk_secret_key,
    authorized_parties=tuple(settings.frontend_origins),
)
# API key authenticator is bound in lifespan after MongoDAL is created


class InterceptHandler(logging.Handler):
    def emit(self, record):
        level = logger.level(record.levelname, no=record.levelno).name
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame, depth = frame.f_back, depth + 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


class HighFrequencyAccessLogFilter(logging.Filter):
    """Filter out high-frequency endpoints from uvicorn access logs.

    Suppresses successful (2xx) requests to relay publish/heartbeat endpoints
    which can generate 20-50+ log lines per agent response during streaming.
    Errors (non-2xx) are still logged for debugging.
    """

    SUPPRESSED_PATHS = ("/relay/hub/", "/heartbeat")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if any(path in message for path in self.SUPPRESSED_PATHS):
            return '" 2' not in message
        return True


logging_config = LOGGING_CONFIG.copy()
logging_config["loggers"]["uvicorn.access"]["handlers"] = ["default"]

logging.getLogger("uvicorn.access").addFilter(HighFrequencyAccessLogFilter())

logger.remove()
if settings.app_env == "development":
    logger.add(
        sys.stderr,
        enqueue=False,
        backtrace=True,  # print full call stack when exception occurs
        diagnose=True,  # variable insight
        serialize=False,  # if want to output JSON, change to True
        level=settings.log_level,
    )
else:
    logger.add(
        f"logs/app_{time.strftime('%Y-%m-%d')}.log",
        enqueue=True,
        backtrace=True,
        diagnose=True,
        serialize=False,
        rotation="100 MB",
        retention="30 days",
        compression="zip",
        level=settings.log_level,
    )


def _assert_startup_bindings_complete(app: FastAPI) -> None:
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
async def lifespan(app: FastAPI):
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

        _mongodb_mod = importlib.import_module("database.mongodb")
        _legacy_mongo = _mongodb_mod.mongodb
        # TODO: Remove this dual MongoDB startup path after remaining legacy
        # database.mongodb consumers move to MongoDALImpl. Until then, the
        # legacy singleton and DAL each maintain their own Motor client pool.
        await _legacy_mongo.connect()

        if _legacy_mongo.client is not None:
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
            from app_shell.context_assembly_service import context_assembly_service
            from app_shell.context_memory_runtime import AppShellMemoryCenter
            from app_shell.debate_service import debate_service
            from app_shell.gemini_service import gemini_service
            from app_shell.inspection_runtime import AppShellInspectionCenter
            from app_shell.memory_search_service import memory_search_service
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
            from app_shell.s3_service import s3_service
            from app_shell.task_service import task_service
            from common.utils.a2a_helpers import bind_a2a_artifact_storage
            from container import (
                create_agent_deps,
                create_agent_resolver_repository,
                create_agent_viewset_vector_index,
                create_api_key_store,
                create_app_shell_repository_store,
                create_context_memory_deps,
                create_context_memory_facade,
                create_delivery_cancellation_collection,
                create_delivery_config,
                create_delivery_deps,
                create_delivery_facade,
                create_delivery_redis_clients,
                create_delivery_startup_policy,
                create_execution_deps,
                create_execution_facade,
                create_execution_repositories,
                create_mongo_dal,
                create_object_storage_dal,
                create_platform_config,
                create_platform_deps,
                create_platform_facade,
                create_room_deps,
                create_vector_dal,
            )
            from context_memory.config import ContextMemoryLLMConfig

            get_db = _mongodb_mod.get_db
            Repository = importlib.import_module("database.repository").Repository
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
                PlatformAttachmentCleanupPort,
                PlatformAttachmentMetadataReader,
            )
            from platform_module.rate_limit import PlatformAgentRateLimiter

            a2a_artifact_storage.bind_a2a_storage_dependencies(
                storage_service=s3_service,
                s3_bucket_name=settings.s3_bucket_name,
                max_file_size_mb=settings.max_file_size_mb,
            )
            bind_a2a_artifact_storage(a2a_artifact_storage)
            await _legacy_mongo.create_context_memory_indexes()
            agent_rate_limiter = PlatformAgentRateLimiter(
                collection=_legacy_mongo.agent_requests_collection,
            )
            viewset.bind_viewset_dependencies(
                provider=AppShellViewSetRepositoryProvider(
                    db_provider=get_db,
                    create_repository=Repository,
                ),
            )

            class AppShellAgentAvatarManager:
                def __init__(self, storage, agent_store) -> None:
                    self._storage = storage
                    self._agent_store = agent_store

                async def store_avatar(
                    self,
                    *,
                    agent_id: str,
                    s3_key: str,
                    content: bytes,
                    content_type: str,
                ) -> str:
                    await self._storage.upload_file(
                        file_data=content,
                        s3_key=s3_key,
                        content_type=content_type,
                        content_length=len(content),
                    )
                    icon_url = self._storage.get_public_url(s3_key)
                    await self._agent_store.agents_collection.update_one(
                        {"agent_id": agent_id},
                        {"$set": {"agent_card.iconUrl": icon_url}},
                    )
                    return icon_url

            agent.bind_agent_dependencies(
                center=AppShellAgentCenter(),
                service=agent_service,
                issue_service=capability_issue_service,
                avatar_manager=AppShellAgentAvatarManager(s3_service, _legacy_mongo),
            )
            inspection_center.bind_inspection_dependencies(AppShellInspectionCenter())
            memory_center.bind_memory_dependencies(AppShellMemoryCenter())
            mongo_dal = create_mongo_dal()
            _mongo_dal = mongo_dal
            app.state.mongo_dal = mongo_dal
            await mongo_dal.connect()
            # Bind Platform-owned API key store after MongoDAL is created
            api_key_store = create_api_key_store(mongo=mongo_dal)
            discovery_api_keys.bind_api_key_store(api_key_store)
            bind_api_key_authenticator(MongoAPIKeyAuthenticator(api_key_store))
            vector_dal = create_vector_dal()
            _delivery_config = create_delivery_config(settings)
            delivery_startup_policy = create_delivery_startup_policy(
                redis_url=settings.redis_url,
                multi_worker=os.environ.get("SERVER_SOFTWARE", "").startswith(
                    "gunicorn"
                ),
            )
            delivery_redis_kv, delivery_redis_pubsub = create_delivery_redis_clients(
                redis_url=settings.redis_url,
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
                a2a_service,
            )

            _db_svc = importlib.import_module("app_shell.database_service").db_service
            from app_shell.hitl_service import (
                bind_hitl_service,
                create_hitl_service,
                hitl_service,
            )

            pinecone_db = importlib.import_module("database.pinecone_db").pinecone_db
            pinecone_db.connect()
            _db_svc.bind_backends(mongo=_legacy_mongo, pinecone=pinecone_db)
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
            llm_gateway_config = LLMGatewayConfig.from_settings(settings)
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
                max_expansion_words=settings.discovery_query_expansion_threshold,
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
            _db_svc.bind_embedding_service(embedding_llm_service)
            openai_service.bind_llm_gateway(
                llm_provider,
                llm_gateway_config,
                discovery_query_expansion_threshold=(
                    settings.discovery_query_expansion_threshold
                ),
                debate_rounds=settings.debate_rounds,
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
            room_runtime.bind_debate_rounds(settings.debate_rounds)
            agent_resolver_service.bind_agent_selection_service(
                agent_selection_llm_service
            )
            memory_search_service.bind_embedding_service(embedding_llm_service)
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
                gateway_base_url=settings.gateway_base_url,
            )
            _agent_facade = _agent_deps.agent_registry
            agent_service.bind_facade(_agent_facade)
            agent_matcher.bind_facade(_agent_facade)
            agent_selection_service.bind_facade(_agent_facade)
            agent_health_service.bind_repository(_agent_deps.agent_repository)
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
            app_shell_store = create_app_shell_repository_store(
                mongo=mongo_dal,
                room_deps=_room_deps,
                agent_deps=_agent_deps,
            )
            membership_source.bind_store(app_shell_store)
            debate_service.bind_store(app_shell_store)
            room_coordinator_service.bind_store(app_shell_store)
            chat_memory_service.bind_store(app_shell_store)
            room_memory_service.bind_store(app_shell_store)
            bind_notification_store(app_shell_store)
            a2a_service.bind_task_db(app_shell_store)
            app_shell_client_request_id_resolver = SSEClientRequestIdResolver(
                resolver=app_shell_store,
            )
            app.state.execution_client_request_id_resolver = (
                app_shell_client_request_id_resolver
            )
            bind_hitl_service(
                create_hitl_service(
                    store=app_shell_store,
                    delivery=HITLDeliveryAdapter(_delivery_deps.event_publisher),
                    a2a_service=a2a_service,
                    continuation=A2AHITLContinuationAdapter(
                        a2a_service,
                        lambda: execution_room_message_center,
                    ),
                    task_notifications=HITLTaskNotificationAdapter(
                        notify_task_update_with_string_state
                    ),
                )
            )
            room_center.bind_room_dependencies(
                center=AppShellRoomCenter(),
                store=app_shell_store,
                selection_service=agent_selection_service,
            )
            a2a_tasks.bind_a2a_task_dependencies(app_shell_store)
            agent_group.bind_agent_group_dependencies(app_shell_store)
            sse.bind_sse_dependencies(app_shell_store, sse_manager)
            room_runtime.bind_store(app_shell_store)
            room_runtime.bind_facade(_room_facade)
            room_runtime.bind_s3_service(s3_service)
            room_center.room_center.bind_facade(_room_facade)
            hitl.bind_room_ownership_reader(_room_facade)
            execution_room_message_center.bind(
                create_room_message_center(
                    room_services=room_services,
                    store=app_shell_store,
                    sse_manager=sse_manager,
                    room_coordinator_service=room_coordinator_service,
                    summary_service=summary_llm_service,
                    notification_service=notification_service,
                    agent_resolver_service=agent_resolver_service,
                    a2a_service=a2a_service,
                    task_service=task_service,
                    room_memory_service=room_memory_service,
                    debate_service=debate_service,
                    rate_limit_service=agent_rate_limiter,
                    room_supervisor_service=room_supervisor_service,
                    hitl_coordinator=hitl_service,
                    task_notifications=TaskNotificationAdapter(
                        notify_task_update_with_string_state
                    ),
                    task_notification_impl=_notify_task_update_impl,
                    agent_health_service=agent_health_service,
                    s3_service=s3_service,
                    capability_issue_service=capability_issue_service,
                    context_assembly_service=context_assembly_service,
                    memory_search_service=memory_search_service,
                    compaction_service=compaction_service,
                    build_turn_content_func=build_turn_content,
                    supervisor_planning_error_cls=SupervisorPlanningError,
                    orphan_threshold_minutes=settings.orphan_threshold_minutes,
                    debate_rounds=settings.debate_rounds,
                    cloud_health_cache_ttl=settings.cloud_health_cache_ttl,
                    cloud_health_check_timeout=settings.cloud_health_check_timeout,
                )
            )
            execution_room_message_center.bind_facade(_room_facade)

            def create_webhook_transport():
                handler = AgentResponseHandler(
                    message_writer=app_shell_store,
                    task_writer=app_shell_store,
                    continuation_store=app_shell_store,
                    client_request_resolver=app_shell_store,
                    room_reader=app_shell_store,
                    hitl_reader=app_shell_store,
                    sse_manager=sse_manager,
                    room_message_center=execution_room_message_center,
                    hitl_coordinator=hitl_service,
                    notification_service=notification_service,
                    task_notification_impl=_notify_task_update_impl,
                )
                handler.bind_execution_event_deps(emit_room_processing_status)
                return WebhookTransport(
                    response_handler=handler,
                    webhook_auth=app_shell_store,
                    message_reader=app_shell_store,
                    cancellation_reader=app_shell_store,
                    task_notifier=notify_task_update_with_string_state,
                )

            webhooks.bind_webhook_dependencies(create_webhook_transport)

            execution_facade = create_execution_facade(
                room_center=room_center.room_center,
                room_message_center=execution_room_message_center,
                hitl_service=hitl_service,
                run_lifecycle=run_lifecycle,
                run_reader=RunQueryAdapter(_execution_repos["run_repository"]),
                cancellation_state=CancellationStateC3Adapter(sse_manager),
                cancellation_store=MongoCancellationStoreAdapter(app_shell_store),
                hitl_message_cancellation=HITLMessageCancellationAdapter(hitl_service),
                agent_task_cleanup=AgentTaskCleanupAdapter(
                    store=app_shell_store,
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
                return await emit_processing_status(
                    **kwargs,
                    run_lifecycle=run_lifecycle,
                    event_publisher=_delivery_deps.event_publisher,
                    run_event_enabled=run_event_sse_enabled,
                    client_request_id_resolver=app_shell_client_request_id_resolver,
                )

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
            object_storage = create_object_storage_dal()
            platform_config = create_platform_config(settings)
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
                default_limit=settings.discovery_default_limit,
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
            _context_memory_deps = create_context_memory_deps(context_memory_facade)
            context_assembly_service.bind_facade(context_memory_facade)
            memory_search_service.bind_facade(context_memory_facade)
            compaction_service.bind_content_storage(platform_facade.content_storage)
            compaction_service.bind_room_memory_reader(context_memory_facade)
            compaction_service.bind_facade(context_memory_facade)
            room_memory_service.bind_facade(context_memory_facade)
            room_runtime.bind_context_memory(_context_memory_deps.memory_manager)
        else:
            logger.warning("AgentDeps binding skipped: MongoDB client is unavailable")

        await _legacy_mongo.ensure_agent_indexes()
        await _legacy_mongo.create_capability_issue_indexes()
        await _legacy_mongo.create_run_lifecycle_indexes()
        await _legacy_mongo.create_room_quotes_indexes()
        if _legacy_mongo.client is not None:
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
        if settings.webhook_signing_key:
            await _legacy_mongo.create_task_tracking_indexes()
            await app_shell_store.ensure_hitl_indexes()

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
            is_gunicorn=os.environ.get("SERVER_SOFTWARE", "").startswith("gunicorn"),
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
            stale_check_minutes=settings.stale_check_minutes,
            task_expiry_hours=settings.task_expiry_hours,
            pending_task_warning_hours=settings.pending_task_warning_hours,
            orphan_threshold_minutes=settings.orphan_threshold_minutes,
            processing_status_expiry_minutes=settings.processing_status_expiry_minutes,
        )
        stale_task_checker.set_runtime_deps(
            StaleTaskCheckerDeps(
                store=app_shell_store,
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
                raw = (os.environ.get("FEATURE_RUN_DUAL_WRITE") or "1").strip().lower()
                return raw not in ("0", "false", "no", "off")

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
                room_memories_collection=_legacy_mongo.room_memories_collection,
                get_room_ids_with_non_terminal_runs=_legacy_mongo.get_room_ids_with_non_terminal_runs,
                compaction_service=compaction_service,
            )
        )
        orphaned_upload_cleaner.set_leader_election(_leader)
        orphaned_upload_cleaner.set_cleanup_deps(
            OrphanedUploadCleanerDeps(
                file_uploads_collection=_legacy_mongo.file_uploads_collection,
                room_user_messages_collection=_legacy_mongo.room_user_messages_collection,
                object_storage=s3_service,
            )
        )

        _bg_started = True
        await agent_health_service.start()

        if settings.webhook_signing_key:
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
            db=app_shell_store,
            sse_manager=sse_manager,
            room_message_center=_rmc,
            hitl_coordinator=hitl_service,
            event_publisher=_delivery_deps.event_publisher if _delivery_deps else None,
            worker_id=(
                _delivery_facade.instance_id if _delivery_facade is not None else None
            ),
            response_converter=hub_agent_response_internal_to_agent_event,
            offline_failure_port=RelayOfflineFailureAdapter(
                app_shell_store,
                sse_manager,
            ),
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
        await _legacy_mongo.close_database_connection()
        raise

    # ── Phase 3: Serve + Normal Shutdown ──
    _assert_startup_bindings_complete(app)
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
            else settings.shutdown_drain_seconds
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

        await _legacy_mongo.close_database_connection()


app = FastAPI(lifespan=lifespan, title="Multi-Agent AI System")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,  # Allow all frontend URLs from env
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "Cache-Control",
        "sentry-trace",
        "baggage",
    ],
)

# Add Discovery, Gateway & Relay API CORS middleware
# This applies permissive CORS to /api/v1/discovery/*, /api/v1/gateway/*, and /api/v1/relay/* paths
# Note: Middleware runs in reverse order, so adding after global CORS makes it run first.
app.add_middleware(DiscoveryCORSMiddleware, api_prefix=settings.api_prefix)


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


# Pure function — trivially testable without lifespan/DB
def compute_health_status(
    *,
    delivery_pubsub_connected: bool,
    delivery_kv_connected: bool,
    legacy_redis_service_connected: bool,
    relay_streams_available: bool = False,
    redis_url: str,
    change_stream_connected: bool,
) -> dict:
    """Compute health status body and HTTP status code."""
    redis_expected = bool(redis_url)
    redis_degraded = redis_expected and not (
        delivery_pubsub_connected
        and delivery_kv_connected
        and legacy_redis_service_connected
        and relay_streams_available
    )
    degraded = redis_degraded or not change_stream_connected
    return {
        "body": {
            "status": "degraded" if degraded else "ok",
            "change_stream_connected": change_stream_connected,
            "delivery_pubsub_connected": delivery_pubsub_connected,
            "delivery_kv_connected": delivery_kv_connected,
            "legacy_redis_service_connected": legacy_redis_service_connected,
            "relay_streams_available": relay_streams_available,
            "redis_expected": redis_expected,
            "broker_connected": delivery_pubsub_connected,
            "broker_expected": redis_expected,
            "redis_service_connected": legacy_redis_service_connected,
        },
        "status_code": 503 if degraded else 200,
    }


health_check_service: HealthCheck = AppShellHealthCheck(
    redis_url=settings.redis_url,
    compute_health_status=compute_health_status,
)


def get_health_check() -> HealthCheck:
    return health_check_service


# Health check endpoint (no prefix, no dependencies)
@app.get("/health")
async def health_check(
    request: Request,
    health: HealthCheck = Depends(get_health_check),
):
    return await health.check(request)


# Include API routers with /api/v1 prefix and global authentication
api_prefix = os.getenv("API_PREFIX", "/api/v1")

app.include_router(api_gateway.router, prefix=api_prefix)


def main() -> None:
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
