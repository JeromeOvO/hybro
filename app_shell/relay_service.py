"""Compatibility imports for the legacy app-shell relay service surface."""

from __future__ import annotations

from typing import Any

from hub_runtime_bridge.compat import relay_service as _impl
from hub_runtime_bridge.compat.relay_service import (
    RelayHubLivenessReader,
    RelayService,
    init_relay_service,
)

__all__ = [
    "RelayHubLivenessReader",
    "RelayService",
    "init_relay_service",
    "relay_service",  # noqa: F822 - provided dynamically by __getattr__.
]


def __getattr__(name: str) -> Any:
    if name == "relay_service":
        return _impl.relay_service
    raise AttributeError(name)
