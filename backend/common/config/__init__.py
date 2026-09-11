"""Shared configuration exports; importing schemas does not read user files."""

from common.config.loader import Settings, get_settings


def __getattr__(name: str) -> object:
    if name == "settings":
        return get_settings()
    raise AttributeError(name)


__all__ = ["Settings", "get_settings", "settings"]
