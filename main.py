import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from uvicorn.config import LOGGING_CONFIG

from api import (
    a2a_tasks,
    agent,
    agent_group,
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


async def _heal_diverged_runs_on_startup() -> None:
    """One-shot sweep: heal any runs where run_events has advanced past the runs head.

    This catches the crash-window divergence (event committed, head update lost)
    that the periodic watchdog would otherwise fail to fix due to DuplicateKeyError.
    """
    from models.run import NON_TERMINAL_RUN_STATE_VALUES
    from services.run_command_handler import run_command_handler

    try:
        cursor = mongodb.runs_collection.find(
            {"state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)}},
        ).limit(500)
        docs = await cursor.to_list(length=500)
    except Exception as e:
        logger.warning("startup heal: failed to query non-terminal runs: {}", e)
        return

    healed = 0
    for doc in docs:
        run_id = str(doc.get("run_id", ""))
        if not run_id:
            continue
        try:
            if await run_command_handler.heal_head_from_events(run_id):
                healed += 1
        except Exception as e:
            logger.warning("startup heal: error healing run {}: {}", run_id, e)

    if healed:
        logger.info("startup heal: healed {} diverged run(s)", healed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to handle startup and shutdown events.

    Startup is split into two phases with a multi-worker safety guard:
      Phase 1 — Infrastructure (DB + Redis, no background work)
      Guard   — Fail if gunicorn without fully connected Redis
      Phase 2 — Background services (only after guard passes)

    Cleanup is split into two separate paths:
      Startup failure — tears down only what was opened, does NOT poison
          singleton state (no set_draining, no stop_change_stream_watcher)
      Normal shutdown — full teardown including draining and change stream
    """
    _redis_service = None
    _redis_streams_service = None
    _leader = None
    _relay_svc = None
    _agent_deps = None
    _change_stream_started = False
    _bg_started = False

    try:
        # ── Phase 1: Infrastructure (DB + Redis, no background work) ──

        await mongodb.connect()
        pinecone_db.connect()

        if mongodb.client is not None:
            from a2a_adapter import AgentCardResolverImpl
            from container import (
                create_agent_deps,
                create_context_memory_deps,
                create_context_memory_facade,
                create_room_deps,
            )
            from context_memory.config import ContextMemoryLLMConfig
            from dal.mongo import MongoDALImpl
            from dal.pinecone import VectorDALImpl
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
            from services.memory_search_service import memory_search_service
            from services.memory_service import room_memory_service
            from services.room_membership_source import LegacyRoomMembershipSeedSource
            from services.room_services import room_services

            await mongodb.create_context_memory_indexes()
            mongo_dal = MongoDALImpl(database=mongodb.db)
            model_registry = ModelRegistryImpl()
            vector_dal = VectorDALImpl()
            llm_provider = LLMGatewayImpl(model_registry=model_registry)
            _agent_deps = create_agent_deps(
                mongo=mongo_dal,
                vector=vector_dal,
                llm_provider=llm_provider,
                card_resolver=AgentCardResolverImpl(),
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
            room_center.room_message_center.bind_facade(_room_facade)

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
        await _heal_diverged_runs_on_startup()
        if settings.webhook_signing_key:
            await mongodb.create_task_tracking_indexes()
            from services.database_service import db_service
            await db_service.ensure_hitl_indexes()

        # Init all three Redis subsystems before the guard
        from infrastructure.brokers import create_event_broker
        from infrastructure.redis_service import create_redis_service

        _event_broker = create_event_broker()
        if _event_broker:
            await sse_manager.start_event_broker(_event_broker)
            logger.info("Event broker started (cross-instance SSE fan-out enabled)")
        else:
            logger.info("Event broker disabled (REDIS_URL not set — single-instance mode)")

        _redis_service = create_redis_service()
        if _redis_service:
            await _redis_service.start()
            await sse_manager.start_redis_service(_redis_service)
            logger.info("RedisService started (shared cancellation/dedup enabled)")
        else:
            logger.info("RedisService disabled (REDIS_URL not set)")

        _redis_streams_service = create_redis_service()  # separate pool for blocking XREAD
        if _redis_streams_service:
            await _redis_streams_service.start()

        # ── Guard: fail if gunicorn without fully connected Redis ──
        check_multi_worker_safety(
            is_gunicorn=os.environ.get("SERVER_SOFTWARE", "").startswith("gunicorn"),
            broker_connected=sse_manager.broker_connected,
            redis_service_connected=bool(
                _redis_service and _redis_service.is_connected
            ),
            relay_streams_connected=bool(
                _redis_streams_service and _redis_streams_service.is_connected
            ),
        )

        # ── Phase 2: Background services (only after guard passes) ──

        from infrastructure.leader_election import LeaderElection
        if _redis_service and _redis_service.is_connected:
            _leader = LeaderElection(
                _redis_service, instance_id=sse_manager._instance_id
            )
            logger.info("Leader election enabled for background jobs")

        agent_health_service.set_leader_election(_leader)
        stale_task_checker.set_leader_election(_leader)
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

        try:
            await sse_manager.start_change_stream_watcher(
                mongodb.cancelled_messages_collection
            )
            _change_stream_started = True
            logger.info("Message cancellation change stream watcher started")
        except Exception as e:
            logger.warning(
                f"Could not start change stream watcher (may not have replica set): {e}"
            )

        await compaction_sweep.start()
        await orphaned_upload_cleaner.start()

        # Initialize relay service
        from services.agent_liveness_service import bind_agent_liveness_deps
        from services.database_service import db_service as _db_svc
        from services.relay_service import (
            RelayHubLivenessReader,
            init_relay_service,
        )
        from modules.RoomMessageCenter import room_message_center as _rmc
        _rmc.set_redis_service(_redis_service)
        _relay_svc = init_relay_service(
            mongo=mongodb, database_service=_db_svc, sse_manager=sse_manager,
            room_message_center=_rmc,
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
        # DO NOT call set_draining() — poisons _draining flag (sse_services.py:163)
        # DO NOT call stop_change_stream_watcher() — sets _shutdown_flag (sse_services.py:730)
        # which start_change_stream_watcher() never resets (sse_services.py:611)
        if _relay_svc:
            await _relay_svc.stop()
        if _redis_streams_service:
            await _redis_streams_service.stop()
        if _bg_started:
            await stale_task_checker.stop()
            await compaction_sweep.stop()
            await orphaned_upload_cleaner.stop()
            await agent_health_service.stop()
        if _change_stream_started and sse_manager._change_stream_task:
            sse_manager._change_stream_task.cancel()
            try:
                await sse_manager._change_stream_task
            except asyncio.CancelledError:
                pass
        if _leader:
            await _leader.release_all(ALL_JOB_NAMES)
        await sse_manager.stop_redis_service()
        if _redis_service:
            await _redis_service.stop()
        await sse_manager.stop_event_broker()
        await mongodb.close_database_connection()
        raise

    # ── Phase 3: Serve + Normal Shutdown ──
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

        # Drain: stop accepting new SSE connections and allow in-flight events to finish
        sse_manager.set_draining(True)
        await asyncio.sleep(settings.shutdown_drain_seconds)

        # Stop RedisService
        await sse_manager.stop_redis_service()
        if _redis_service:
            await _redis_service.stop()

        # Stop event broker
        await sse_manager.stop_event_broker()

        # Stop change stream watcher
        await sse_manager.stop_change_stream_watcher()

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
    broker_connected: bool,
    redis_service_connected: bool,
    relay_streams_connected: bool,
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
    if not broker_connected:
        problems.append("Event broker (Pub/Sub) not connected")
    if not redis_service_connected:
        problems.append("RedisService (key-value) not connected")
    if not relay_streams_connected:
        problems.append("Relay streams not connected")

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
    *, broker_connected: bool, redis_url: str, change_stream_connected: bool,
    redis_service_connected: bool = False,
    relay_streams_available: bool = False,
) -> dict:
    """Compute health status body and HTTP status code."""
    broker_expected = bool(redis_url)
    degraded = broker_expected and not broker_connected
    return {
        "body": {
            "status": "degraded" if degraded else "ok",
            "change_stream_connected": change_stream_connected,
            "broker_connected": broker_connected,
            "broker_expected": broker_expected,
            "redis_service_connected": redis_service_connected,
            "relay_streams_available": relay_streams_available,
        },
        "status_code": 503 if degraded else 200,
    }


# Health check endpoint (no prefix, no dependencies)
@app.get("/health")
async def health_check():
    from services.relay_service import relay_service as _relay_svc_health
    result = compute_health_status(
        broker_connected=sse_manager.broker_connected,
        redis_url=settings.redis_url,
        change_stream_connected=sse_manager.change_stream_connected,
        redis_service_connected=sse_manager.redis_connected,
        relay_streams_available=bool(
            _relay_svc_health
            and _relay_svc_health._streams
            and _relay_svc_health._streams.is_connected
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
