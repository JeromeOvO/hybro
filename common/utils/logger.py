"""Logging utilities for the multi-agents backend."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
JSON_LOG_FORMAT = (
    '{"timestamp":"%(asctime)s","logger":"%(name)s",'
    '"level":"%(levelname)s","message":"%(message)s"}'
)


def _resolve_log_format(value: str | None) -> str:
    if value is None:
        return DEFAULT_LOG_FORMAT
    if value.strip().lower() == "json":
        return JSON_LOG_FORMAT
    return value


(log_path := Path(os.getenv("LOG_PATH"))).parent.mkdir(parents=True, exist_ok=True)
log_path.touch()

file_handler = RotatingFileHandler(
    log_path,
    maxBytes=int(os.getenv("LOG_MAX_BYTES", "10485760")),  # 10MB default
    backupCount=int(os.getenv("LOG_BACKUP_COUNT", "5")),
)
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format=_resolve_log_format(os.getenv("LOG_FORMAT")),
)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with file handler configured."""
    logger = logging.getLogger(name)
    logger.addHandler(file_handler)
    return logger
