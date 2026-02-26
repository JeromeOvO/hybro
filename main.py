import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from uvicorn.config import LOGGING_CONFIG

from api import (
    a2a_tasks,
    agent,
    agent_group,
    discovery,
    inspection_center,
    memory_center,
    orchestration_center,
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
from jobs.compaction_sweep import compaction_sweep
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


logging_config = LOGGING_CONFIG.copy()
logging_config["loggers"]["uvicorn.access"]["handlers"] = ["default"]

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to handle startup and shutdown events."""
    # await init_db(app)        # gets "app" from FastAPI
    await mongodb.connect()
    pinecone_db.connect()

    # Start the agent health check service
    await agent_health_service.start()

    # Initialize task tracking indexes for room_agent_messages
    if settings.webhook_signing_key:
        await mongodb.create_task_tracking_indexes()
        # Start stale task checker background job
        await stale_task_checker.start()
        # Run cleanup immediately on startup to recover tasks orphaned by a
        # previous server restart, instead of waiting for the first interval.
        await stale_task_checker.check_stale_tasks()
        logger.info(
            "A2A long-running tasks support initialized (using room_agent_messages)"
        )
    else:
        logger.warning("WEBHOOK_SIGNING_KEY not set - A2A long-running tasks disabled")

    # Start change stream watcher for message cancellations
    try:
        await sse_manager.start_change_stream_watcher(
            mongodb.cancelled_messages_collection
        )
        logger.info("Message cancellation change stream watcher started")
    except Exception as e:
        logger.warning(
            f"Could not start change stream watcher (may not have replica set): {e}"
        )

    # Start background compaction sweep (§6 lossless compaction)
    await compaction_sweep.start()

    try:
        yield
    finally:
        # Stop the stale task checker
        await stale_task_checker.stop()

        # Stop background compaction sweep
        await compaction_sweep.stop()

        # Stop the agent health check service
        await agent_health_service.stop()

        # Stop change stream watcher
        await sse_manager.stop_change_stream_watcher()

        # await close_db(app)
        await mongodb.close_database_connection()


app = FastAPI(lifespan=lifespan, title="Multi-Agent AI System")

# Add Discovery API CORS middleware
# This applies permissive CORS only to /api/v1/discovery/* paths
# Note: Middleware runs in reverse order, so adding first means it runs last
app.add_middleware(DiscoveryCORSMiddleware)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,  # Allow all frontend URLs from env
    allow_credentials=True, 
    allow_methods=["*"],
    allow_headers=["*"]
)


# Health check endpoint (no prefix, no dependencies)
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "change_stream_connected": sse_manager.change_stream_connected,
    }


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

# Discovery API - External public API with API key auth 
# Uses open CORS to allow external access from any origin
app.include_router(
    discovery.router,
    prefix=api_prefix,
    tags=["discovery"],
    # Auth handled per-route via X-API-Key header in discovery.py
)

app.include_router(
    a2a_tasks.router,
    prefix=api_prefix,
    tags=["a2a_tasks"],
    # Auth handled per-route in a2a_tasks.py
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
