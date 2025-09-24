import logging
import os
import sys

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from uvicorn.config import LOGGING_CONFIG

from api import (  # pylint: disable=import-error
    agent,
    chat,
    inspection_center,
    memory_center,
    orchestration_center,
    room_center,
    task,
)
from database.mongodb import mongodb
from database.pinecone_db import pinecone_db

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
logger.add(
    sys.stderr,
    enqueue=True,  # multi-thread/multi-process safe
    backtrace=True,  # print full call stack when exception occurs
    diagnose=True,  # variable insight
    serialize=False,  # if want to output JSON, change to True
)

app = FastAPI(title="Multi-Agent AI System")

# Add CORS middleware
frontend_urls = os.getenv("FRONTEND_ORIGINS", "http://localhost:3000").split(",")
frontend_urls = [url.strip() for url in frontend_urls if url.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_urls,  # Allow all frontend URLs from env
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Startup and shutdown events
@app.on_event("startup")
async def startup_db_client():
    await mongodb.connect()
    pinecone_db.connect()


@app.on_event("shutdown")
async def shutdown_db_client():
    await mongodb.close_database_connection()


# Health check endpoint (no prefix)
@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Include API routers with /api/v1 prefix
api_prefix = os.getenv("API_PREFIX", "/api/v1")
app.include_router(agent.router, prefix=api_prefix, tags=["agent"])
app.include_router(chat.router, prefix=api_prefix, tags=["chat"])
app.include_router(inspection_center.router, prefix=api_prefix, tags=["inspection"])
app.include_router(memory_center.router, prefix=api_prefix, tags=["memory"])
app.include_router(
    orchestration_center.router, prefix=api_prefix, tags=["orchestration"]
)
app.include_router(room_center.router, prefix=api_prefix, tags=["room"])
app.include_router(task.router, prefix=api_prefix, tags=["task"])
