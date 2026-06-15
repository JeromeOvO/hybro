"""Compatibility facade for domain alias generation.

Production implementation moved to ``agent.domain_alias`` and repository-backed
protocols. This shim keeps existing import paths stable while avoiding direct
legacy MongoDB usage.
"""

from __future__ import annotations

from agent.domain_alias import DomainAliasService

domain_alias_service: DomainAliasService | None = None


def bind_domain_alias_service(service: DomainAliasService) -> None:
    """Bind a domain-alias service implementation."""

    global domain_alias_service
    domain_alias_service = service


def get_domain_alias_service() -> DomainAliasService:
    if domain_alias_service is None:
        raise RuntimeError("DomainAliasService has not been bound")
    return domain_alias_service


__all__ = [
    "DomainAliasService",
    "bind_domain_alias_service",
    "get_domain_alias_service",
]
