"""Compatibility shim for agent resolver services."""

from __future__ import annotations

from agent.resolver import (
    AgentResolverFacadeRepository,
    AgentResolverService,
    ResolveResult,
    _agent_to_routing_candidate,
    _HealthCache,
)

agent_resolver_service = AgentResolverService()

__all__ = [
    "AgentResolverFacadeRepository",
    "AgentResolverService",
    "ResolveResult",
    "_HealthCache",
    "_agent_to_routing_candidate",
    "agent_resolver_service",
]
