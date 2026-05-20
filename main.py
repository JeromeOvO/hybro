import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from a2a.types import TaskState
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from uvicorn.config import LOGGING_CONFIG

from api import (
    a2a_tasks,
    agent,
    agent_group,
    agent_viewset,
    discovery,
    discovery_api_keys,
    files,
    gateway,
    hitl,
    hub,
    inspection_center,
    memory_center,
    orchestration_center,
    relay,
    room_center,
    sse,
    task,
    viewset,
    webhooks,
)
from common.auth import get_current_user
from common.middleware.discovery_cors_middleware import DiscoveryCORSMiddleware
from config.settings import settings
from database.mongodb import mongodb
from database.pinecone_db import pinecone_db
from jobs.cleanup_orphaned_uploads import orphaned_upload_cleaner
from jobs.compaction_sweep import compaction_sweep
from jobs.constants import ALL_JOB_NAMES
from jobs.stale_task_checker import stale_task_checker
from services.agent_health_service import agent_health_service
from services.sse_services import sse_manager

load_dotenv()


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
            return "\" 2" not in message
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

    from services.hitl_service import hitl_service

    if getattr(hitl_service, "_service", None) is None:
        errors.append("hitl_service")

    if getattr(app.state, "execution_deps", None) is None:
        errors.append("app.state.execution_deps")

    if getattr(app.state, "platform_facade", None) is None:
        errors.append("app.state.platform_facade")
    if getattr(gateway, "gateway_service", None) is None:
        errors.append("api.gateway.gateway_service")
    if getattr(files, "file_storage", None) is None:
        errors.append("api.files.file_storage")
    if getattr(relay, "relay_service", None) is None:
        errors.append("api.relay.relay_service")

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
    _redis_service = None
    _redis_streams_service = None
    _leader = None
    _relay_svc = None
    _agent_deps = None
    _delivery_facade = None
    _delivery_config = None
    _execution_deps = None
    _delivery_bound = False
    _bg_started = False

    try:
        # ── Phase 1: Infrastructure (DB + Redis, no background work) ──

        await mongodb.connect()
        pinecone_db.connect()

        if mongodb.client is not None:
            from a2a_adapter import AgentCardResolverImpl, AgentTransportImpl
            from container import (
                create_agent_deps,
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
                create_mongo_dal,
                create_object_storage_dal,
                create_platform_config,
                create_platform_deps,
                create_platform_facade,
                create_room_deps,
                create_vector_dal,
            )
            from context_memory.config import ContextMemoryLLMConfig
            from llm_gateway import LLMGatewayImpl, ModelRegistryImpl
            from services.agent_capability_issue_service import (
                CapabilityIssueExclusionReader,
            )
            from services.agent_matcher import agent_matcher
            from services.agent_selection_service import agent_selection_service
            from services.agent_service import agent_service
            from services.compaction_service import compaction_service
            from services.content_storage_service import content_storage_service
            from services.context_assembly_service import context_assembly_service
            from services.discovery_rate_limit_service import discovery_rate_limit_service
            from services.discovery_service import discovery_service
            from services.memory_search_service import memory_search_service
            from services.memory_service import room_memory_service
            from services.room_membership_source import LegacyRoomMembershipSeedSource
            from services.room_services import room_services
            from services.gateway_rate_limit_service import gateway_rate_limit_service
            from services.openai_service import openai_service
            from modules.InspectionCenter import InspectionCenter
            from modules.MemoryCenter import MemoryCenter
            from modules.TaskCenter import TaskCenter
            from modules.WorkflowCenter import workflow_center
            from database.mongodb import get_db
            from database.repository import Repository

            await mongodb.create_context_memory_indexes()
            viewset.bind_viewset_dependencies(
                get_db=get_db,
                create_repository=Repository,
            )
            agent_viewset.bind_agent_viewset_dependencies(
                openai_service=openai_service,
                pinecone_db=pinecone_db,
            )
            inspection_center.bind_inspection_dependencies(InspectionCenter())
            memory_center.bind_memory_dependencies(MemoryCenter())
            orchestration_center.bind_orchestration_dependencies(workflow_center)
            task.bind_task_dependencies(TaskCenter())
            discovery_api_keys.bind_api_key_store(mongodb)
            mongo_dal = create_mongo_dal(database=mongodb.db)
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

            from execution.legacy_processing_status import (
                LegacyProcessingStatusC3Adapter,
                SSEClientRequestIdResolver,
            )
            from execution.events import (
                emit_processing_status,
                run_event_notification_from_payload,
            )
            from execution.dispatch.task_notifications import TaskNotificationAdapter
            from execution.hitl.adapters import (
                A2AHITLContinuationAdapter,
                HITLTaskNotificationAdapter,
            )
            from execution.cancellation import (
                AgentTaskCleanupAdapter,
                CancellationStateC3Adapter,
                HITLMessageCancellationAdapter,
                MongoCancellationStoreAdapter,
            )
            from execution.orchestration.factory import (
                create_room_message_center,
                room_message_center as execution_room_message_center,
            )
            from execution.run_lifecycle import RunLifecycleAdapter
            from execution.run_queries import RunQueryAdapter
            from services.a2a_service import a2a_service
            from services.database_service import db_service as _db_svc
            from services.hitl_service import (
                bind_hitl_service,
                create_hitl_service,
                hitl_service,
            )
            from services.run_command_handler import (
                run_command_handler,
                run_event_sse_enabled,
            )
            from services.task_notification_service import notify_task_update
            a2a_tasks.bind_a2a_task_dependencies(_db_svc)
            agent_group.bind_agent_group_dependencies(_db_svc)
            sse.bind_sse_dependencies(_db_svc, sse_manager)

            async def notify_task_update_with_string_state(**kwargs):
                state = kwargs.get("state")
                if isinstance(state, str):
                    kwargs["state"] = TaskState(state)
                return await notify_task_update(**kwargs)

            bind_hitl_service(
                create_hitl_service(
                    database_service=_db_svc,
                    sse_manager=sse_manager,
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
            run_lifecycle = RunLifecycleAdapter(
                command_handler=run_command_handler,
                runs_collection=mongodb.runs_collection,
            )
            legacy_processing_status_publisher = LegacyProcessingStatusC3Adapter(
                sse_manager=sse_manager,
            )
            app_shell_client_request_id_resolver = SSEClientRequestIdResolver(
                db_service=_db_svc,
            )
            app.state.execution_run_lifecycle = run_lifecycle
            app.state.execution_legacy_processing_status_publisher = (
                legacy_processing_status_publisher
            )
            app.state.execution_client_request_id_resolver = (
                app_shell_client_request_id_resolver
            )

            model_registry = ModelRegistryImpl()
            llm_provider = LLMGatewayImpl(model_registry=model_registry)
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
            agent_health_service.bind_facade(_agent_facade)

            _room_deps = create_room_deps(
                mongo=mongo_dal,
                agent_registry=_agent_deps.agent_registry,
                membership_source=LegacyRoomMembershipSeedSource(),
            )
            _room_facade = _room_deps.room_registry
            room_services.bind_facade(_room_facade)
            room_center.room_center.bind_facade(_room_facade)
            execution_room_message_center.bind(
                create_room_message_center(
                    hitl_coordinator=hitl_service,
                    task_notifications=TaskNotificationAdapter(
                        notify_task_update_with_string_state
                    ),
                )
            )
            room_center.room_message_center.bind_facade(_room_facade)

            execution_facade = create_execution_facade(
                room_center=room_center.room_center,
                room_message_center=room_center.room_message_center,
                hitl_service=hitl_service,
                run_lifecycle=run_lifecycle,
                run_reader=RunQueryAdapter(mongodb.runs_collection),
                cancellation_state=CancellationStateC3Adapter(sse_manager),
                cancellation_store=MongoCancellationStoreAdapter(mongodb),
                hitl_message_cancellation=HITLMessageCancellationAdapter(hitl_service),
                agent_task_cleanup=AgentTaskCleanupAdapter(
                    db_service=_db_svc,
                    get_agent_card_from_url=a2a_service.get_agent_card_from_url,
                    cancel_remote_task=a2a_service.cancel_remote_task,
                    notify_task_update=notify_task_update_with_string_state,
                ),
                agent_response_handler=room_center.room_message_center.agent_response_handler,
                event_publisher=_delivery_deps.event_publisher,
                legacy_processing_status_publisher=legacy_processing_status_publisher,
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
                    legacy_processing_status_publisher=legacy_processing_status_publisher,
                    run_event_enabled=run_event_sse_enabled,
                    client_request_id_resolver=app_shell_client_request_id_resolver,
                )

            room_services.bind_hitl_pending_checker(hitl_service.get_pending_requests)
            room_services.bind_active_run_reader(read_room_active_runs)
            room_services.bind_execution_event_deps(
                processing_status_emitter=emit_room_processing_status,
            )
            room_center.room_message_center.bind_execution_event_deps(
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
                logger=logger,
            )
            platform_facade = create_platform_facade(
                config=platform_config,
                deps=platform_deps,
            )

            async def verify_file_upload_room_ownership(room_id: str, user) -> None:
                if not room_id:
                    raise HTTPException(status_code=400, detail="room_id is required")
                owner_id = await _room_deps.room_registry.get_room_owner(room_id)
                if owner_id is None:
                    raise HTTPException(status_code=404, detail="Room not found")
                if owner_id != user.user_id:
                    raise HTTPException(
                        status_code=403,
                        detail="You do not have permission to access this room",
                    )

            gateway.bind_gateway_dependencies(
                platform_facade.gateway_service,
                gateway_rate_limit_service,
            )
            discovery.bind_discovery_dependencies(
                discovery_service,
                discovery_rate_limit_service,
                default_limit=settings.discovery_default_limit,
            )
            files.bind_file_dependencies(
                platform_facade.file_storage,
                verify_file_upload_room_ownership,
            )
            app.state.platform_facade = platform_facade
            app.state.platform_deps = platform_deps
            # TODO(phase-6/7): Register ContextMemoryEventHandler with EventPublisher
            # once Delivery wires runtime MessageCommitted delivery. Phase 5 keeps the
            # direct compaction call path via legacy services.
            _context_memory_deps = create_context_memory_deps(context_memory_facade)
            context_assembly_service.bind_facade(context_memory_facade)
            memory_search_service.bind_facade(context_memory_facade)
            content_storage_service.bind_facade(context_memory_facade)
            compaction_service.bind_facade(context_memory_facade)
            room_memory_service.bind_facade(context_memory_facade)
            room_services.bind_context_memory(_context_memory_deps.memory_manager)
        else:
            logger.warning("AgentDeps binding skipped: MongoDB client is unavailable")

        await mongodb.ensure_agent_indexes()
        await mongodb.create_capability_issue_indexes()
        await mongodb.create_run_lifecycle_indexes()
        if mongodb.client is not None:
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
        if settings.webhook_signing_key:
            await mongodb.create_task_tracking_indexes()
            from services.database_service import db_service
            await db_service.ensure_hitl_indexes()

        # Init legacy Redis subsystems before the guard. Delivery-owned
        # Pub/Sub/KV clients are constructed through container.py above.
        from infrastructure.redis_service import create_redis_service

        _redis_service = create_redis_service()
        if _redis_service:
            await _redis_service.start()
            logger.info("RedisService started (leader election/relay enabled)")
        else:
            logger.info("RedisService disabled (REDIS_URL not set)")
        app.state.legacy_redis_service = _redis_service

        _redis_streams_service = create_redis_service()  # separate pool for blocking XREAD
        if _redis_streams_service:
            await _redis_streams_service.start()
        app.state.redis_streams_service = _redis_streams_service

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

        from infrastructure.leader_election import LeaderElection
        if _redis_service and _redis_service.is_connected:
            _leader = LeaderElection(
                _redis_service,
                instance_id=(
                    _delivery_facade.instance_id
                    if _delivery_facade is not None
                    else sse_manager._instance_id
                ),
            )
            logger.info("Leader election enabled for background jobs")

        agent_health_service.set_leader_election(_leader)
        stale_task_checker.set_leader_election(_leader)
        if _execution_deps is not None:
            from jobs.stale_task_checker import (
                StaleHITLDeps,
                StaleRecoveryDeps,
                StaleRunWatchdogEventDeps,
            )

            def run_dual_write_enabled() -> bool:
                raw = (
                    os.environ.get("FEATURE_RUN_DUAL_WRITE") or "1"
                ).strip().lower()
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
                    legacy_details=details,
                    run_lifecycle=run_lifecycle,
                    event_publisher=_delivery_deps.event_publisher,
                    legacy_processing_status_publisher=legacy_processing_status_publisher,
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
        orphaned_upload_cleaner.set_leader_election(_leader)

        _bg_started = True
        await agent_health_service.start()

        if settings.webhook_signing_key:
            await stale_task_checker.start()
            await stale_task_checker.check_stale_tasks()
            logger.info(
                "A2A long-running tasks support initialized (using room_agent_messages)"
            )
        else:
            logger.warning("WEBHOOK_SIGNING_KEY not set - A2A long-running tasks disabled")

        await compaction_sweep.start()
        await orphaned_upload_cleaner.start()

        # Initialize relay service
        from services.agent_liveness_service import bind_agent_liveness_deps
        from services.database_service import db_service as _db_svc
        from services.relay_service import (
            RelayHubLivenessReader,
            init_relay_service,
        )
        from execution.facade import hub_agent_response_internal_to_agent_event
        from modules.RoomMessageCenter import room_message_center as _rmc
        _rmc.set_redis_service(_redis_service)
        _relay_svc = init_relay_service(
            mongo=mongodb, database_service=_db_svc, sse_manager=sse_manager,
            room_message_center=_rmc,
            hitl_coordinator=hitl_service,
            event_publisher=_delivery_deps.event_publisher if _delivery_deps else None,
            worker_id=(
                _delivery_facade.instance_id
                if _delivery_facade is not None
                else None
            ),
            response_converter=hub_agent_response_internal_to_agent_event,
        )
        relay.bind_relay_dependencies(_relay_svc)
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
        await _relay_svc.start()
        logger.info("Relay service initialized and heartbeat checker started")

        # Attach Redis Streams to relay service
        if _redis_streams_service and _redis_streams_service.is_connected:
            from infrastructure.relay_streams import RelayStreamService
            _relay_streams = RelayStreamService(
                _redis_streams_service,
                maxlen=settings.relay_stream_maxlen,
                heartbeat_ttl=settings.relay_hub_heartbeat_ttl,
            )
            _relay_svc.set_stream_service(_relay_streams)
            logger.info("Redis Streams relay enabled (separate pool for blocking XREAD)")

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
        try:
            if _delivery_facade is not None:
                await _delivery_facade.stop()
        finally:
            if _delivery_bound:
                sse_manager.unbind_facade()
            app.state.delivery_facade = None
        await mongodb.close_database_connection()
        raise

    # ── Phase 3: Serve + Normal Shutdown ──
    _assert_startup_bindings_complete(app)
    try:
        yield
    finally:
        # Stop the relay service heartbeat checker
        from services.relay_service import relay_service as _relay_svc_shutdown
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

        await mongodb.close_database_connection()


app = FastAPI(lifespan=lifespan, title="Multi-Agent AI System")

# Add Discovery, Gateway & Relay API CORS middleware
# This applies permissive CORS to /api/v1/discovery/*, /api/v1/gateway/*, and /api/v1/relay/* paths
# Note: Middleware runs in reverse order, so adding first means it runs last
app.add_middleware(DiscoveryCORSMiddleware)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,  # Allow all frontend URLs from env
    allow_credentials=True, 
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "Cache-Control", "sentry-trace", "baggage"]
)


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
            "Running under gunicorn requires all Redis services. "
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


# Health check endpoint (no prefix, no dependencies)
@app.get("/health")
async def health_check(request: Request):
    from services.relay_service import relay_service as _relay_svc_health
    delivery_facade = getattr(request.app.state, "delivery_facade", None)
    if delivery_facade is not None:
        await delivery_facade.refresh_health()
    legacy_redis_service = getattr(request.app.state, "legacy_redis_service", None)
    result = compute_health_status(
        delivery_pubsub_connected=bool(
            delivery_facade and delivery_facade.delivery_pubsub_connected
        ),
        delivery_kv_connected=bool(
            delivery_facade and delivery_facade.delivery_kv_connected
        ),
        legacy_redis_service_connected=bool(
            legacy_redis_service and legacy_redis_service.is_connected
        ),
        relay_streams_available=bool(
            _relay_svc_health
            and _relay_svc_health._streams
            and _relay_svc_health._streams.is_connected
        ),
        redis_url=settings.redis_url,
        change_stream_connected=bool(
            delivery_facade and delivery_facade.change_stream_connected
        ),
    )
    return JSONResponse(content=result["body"], status_code=result["status_code"])


# Include API routers with /api/v1 prefix and global authentication
api_prefix = os.getenv("API_PREFIX", "/api/v1")

# Add global authentication dependency to all routers
# This requires authentication for ALL API endpoints under /api/v1
# Agent router has mixed auth - some endpoints are public (GET), some require auth (POST/DELETE)
app.include_router(
    agent.router,
    prefix=api_prefix,
    tags=["agent"],
    # No global auth - handled per-route in agent.py
)
app.include_router(
    inspection_center.router,
    prefix=api_prefix,
    tags=["inspection"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    memory_center.router,
    prefix=api_prefix,
    tags=["memory"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    orchestration_center.router,
    prefix=api_prefix,
    tags=["orchestration"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    room_center.router,
    prefix=api_prefix,
    tags=["room"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    hitl.router,
    prefix=api_prefix,
    tags=["hitl"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    hub.router,
    prefix=api_prefix,
    tags=["hub"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    task.router,
    prefix=api_prefix,
    tags=["task"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    sse.router,
    prefix=api_prefix,
    tags=["sse"],
    # SSE endpoints handle auth via get_current_user_with_query_token (supports ?token= for EventSource)
)
app.include_router(
    agent_group.router,
    prefix=api_prefix,
    tags=["agent_group"],
    dependencies=[Depends(get_current_user)],
)

app.include_router(
    files.router,
    prefix=api_prefix,
    tags=["files"],
    dependencies=[Depends(get_current_user)],
)

# Discovery API - External public API with API key auth 
# Uses open CORS to allow external access from any origin
app.include_router(
    discovery.router,
    prefix=api_prefix,
    tags=["discovery"],
    # Auth handled per-route via X-API-Key header in discovery.py
)

app.include_router(
    discovery_api_keys.router,
    prefix=api_prefix,
    tags=["api_keys"],
)

app.include_router(
    a2a_tasks.router,
    prefix=api_prefix,
    tags=["a2a_tasks"],
    # Auth handled per-route in a2a_tasks.py
)

# Gateway API - External public API with API key auth
# Uses open CORS to allow external SDK/hub access from any origin
app.include_router(
    gateway.router,
    prefix=api_prefix,
    tags=["gateway"],
    # Auth handled per-route via X-API-Key header in gateway.py
)
# Relay API - Hub communication endpoints with API key / JWT auth
# Uses open CORS to allow hub daemon access from any origin
app.include_router(
    relay.router,
    prefix=api_prefix,
    tags=["relay"],
    # Auth handled per-route via X-API-Key or Bearer token in relay.py
)
# Webhook endpoint - no auth prefix, no authentication (uses token validation)
app.include_router(
    webhooks.router,
    prefix=api_prefix,
    tags=["webhooks"],
    # No auth - webhook uses Bearer token validation
)
# For APIs that do not require authentication (user is optional)
# app.include_router(
#     router,
#     prefix=api_prefix,
#     tags=["public-apis"],
#     dependencies=[Depends(get_optional_user)]
# )
