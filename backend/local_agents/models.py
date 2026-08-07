from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class DiscoveryTrigger(StrEnum):
    STARTUP = "startup"
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class LocalAgentDiscoveryResult(BaseModel):
    trigger: DiscoveryTrigger
    open_ports: int = 0
    agents_found: int = 0
    agents_added: int = 0
    agents_reactivated: int = 0
    agents_deactivated: int = 0
    duration_ms: int = 0
    reused_running_discovery: bool = False


__all__ = ["DiscoveryTrigger", "LocalAgentDiscoveryResult"]
