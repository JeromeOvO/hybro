"""Logging utilities for the multi-agents backend."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from common.config import settings

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


(log_path := Path(settings.log_path)).parent.mkdir(parents=True, exist_ok=True)
log_path.touch()

file_handler = RotatingFileHandler(
    log_path,
    maxBytes=settings.log_max_bytes,
    backupCount=settings.log_backup_count,
)
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format=_resolve_log_format(settings.log_format),
)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with file handler configured."""
    logger = logging.getLogger(name)
    logger.addHandler(file_handler)
    return logger
