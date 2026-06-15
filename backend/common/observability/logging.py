import logging


def configure_logging(settings=None) -> None:
    """Compatibility hook for future structured logging setup."""
    return None


def get_logger(name: str) -> logging.Logger:
    from common.utils.logger import get_logger as _get_logger

    return _get_logger(name)


__all__ = ["configure_logging", "get_logger"]
