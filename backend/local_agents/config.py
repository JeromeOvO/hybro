from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocalAgentDiscoveryConfig:
    enabled: bool = False
    host: str = "host.docker.internal"
    port_start: int = 1024
    port_end: int = 65535
    interval_seconds: int = 120
    connect_timeout_seconds: float = 0.05
    probe_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        if not 1 <= self.port_start <= self.port_end <= 65535:
            raise ValueError("local agent discovery port range must be within 1..65535")
        if self.interval_seconds <= 0:
            raise ValueError("local agent discovery interval must be positive")
        if self.connect_timeout_seconds <= 0 or self.probe_timeout_seconds <= 0:
            raise ValueError("local agent discovery timeouts must be positive")


__all__ = ["LocalAgentDiscoveryConfig"]
