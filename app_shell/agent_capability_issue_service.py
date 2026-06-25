"""Compatibility shim for capability-issue services."""

from __future__ import annotations

from agent.capability_issue import (
    AgentCapabilityIssueServiceAdapter,
    AgentCapabilityIssueServiceNotBound,
)
from agent.capability_issue import (
    CapabilityIssueExclusionReader as _CapabilityIssueExclusionReader,
)

capability_issue_service = AgentCapabilityIssueServiceAdapter()


class CapabilityIssueExclusionReader(_CapabilityIssueExclusionReader):
    def __init__(self, service=None) -> None:
        super().__init__(service or capability_issue_service)


__all__ = [
    "AgentCapabilityIssueServiceAdapter",
    "AgentCapabilityIssueServiceNotBound",
    "CapabilityIssueExclusionReader",
    "capability_issue_service",
]
