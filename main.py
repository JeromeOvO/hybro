import logging
import sys
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from uvicorn.config import LOGGING_CONFIG

import api_gateway
from common.auth import bind_auth_config
from common.config.settings import settings
from common.middleware.discovery_cors_middleware import DiscoveryCORSMiddleware
from container import (
    create_application_runtime,
    create_health_check_service,
    shutdown_runtime,
    startup_runtime,
    validate_runtime_bindings,
)

load_dotenv()
bind_auth_config(
    clerk_secret_key_value=settings.clerk_secret_key,
    authorized_parties=tuple(settings.frontend_origins),
)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = create_application_runtime(settings)
    try:
        await startup_runtime(app, runtime)
        validate_runtime_bindings(app, runtime)
        yield
    finally:
        await shutdown_runtime(app, runtime)

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


health_check_service = create_health_check_service(
    redis_url=settings.redis_url,
    compute_health_status=compute_health_status,
)


def get_health_check():
    return health_check_service


# Health check endpoint (no prefix, no dependencies)
@app.get("/health")
async def health_check(
    request: Request,
    health=Depends(get_health_check),
):
    return await health.check(request)


# Include API routers with /api/v1 prefix and global authentication
api_prefix = settings.api_prefix

app.include_router(api_gateway.router, prefix=api_prefix)


def main() -> None:
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
