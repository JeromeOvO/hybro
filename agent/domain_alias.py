from __future__ import annotations

from collections.abc import Callable

from agent.public_url import PublicUrlGenerator, RESERVED_SUBDOMAINS
from common.protocols import AgentRepository


class DomainAliasServiceNotBound(RuntimeError):
    """Raised when the domain alias generator is used without a repository."""


class DomainAliasService:
    """Agent-owned domain alias service."""

    def __init__(
        self,
        *,
        repository: AgentRepository | None = None,
        base_domain: str = "hybro.ai",
        protocol: str = "https",
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._base_domain = base_domain
        self._protocol = protocol
        self._id_factory = id_factory
        self._public_url_generator = None
        if repository is not None:
            self.bind_repository(
                repository,
                base_domain=base_domain,
                protocol=protocol,
            )

    def bind_repository(
        self,
        repository: AgentRepository,
        base_domain: str | None = None,
        protocol: str | None = None,
    ) -> None:
        self._public_url_generator = PublicUrlGenerator(
            exists=repository.public_url_exists,
            base_domain=base_domain or self._base_domain,
            protocol=protocol or self._protocol,
            id_factory=self._id_factory or self._default_id_factory,
        )
        if base_domain is not None:
            self._base_domain = base_domain
        if protocol is not None:
            self._protocol = protocol
        self._repository = repository

    @staticmethod
    def _default_id_factory() -> str:
        import uuid

        return str(uuid.uuid4())

    @property
    def base_domain(self) -> str:
        return self._base_domain

    @property
    def protocol(self) -> str:
        return self._protocol

    def _get_repository(self) -> AgentRepository:
        if not hasattr(self, "_repository"):
            raise DomainAliasServiceNotBound(
                "Domain alias service is not bound. Bind a repository before use."
            )
        return self._repository

    async def generate_public_url(
        self,
        agent_name: str,
        agent_id: str,
        preferred_subdomain: str | None = None,
    ) -> str:
        generator = self._generator()
        return await generator.generate_public_url(
            agent_name=agent_name,
            agent_id=agent_id,
            preferred_subdomain=preferred_subdomain,
        )

    async def get_public_url_by_agent_id(self, agent_id: str) -> str | None:
        repository = self._get_repository()
        doc = await repository.get_by_id(agent_id)
        if doc is None:
            return None
        return doc.get("public_url")

    def is_masked_url(self, url: str) -> bool:
        if not url:
            return False
        url_lower = url.lower()
        base_domain = self._base_domain.lower()
        if base_domain not in url_lower:
            return False
        prefixes = (
            f"https://api.{base_domain}",
            f"http://api.{base_domain}",
            f"https://www.{base_domain}",
            f"http://www.{base_domain}",
            f"https://{base_domain}",
            f"http://{base_domain}",
        )
        if any(url_lower.startswith(prefix) for prefix in prefixes):
            return False
        return True

    def _generator(self) -> PublicUrlGenerator:
        generator = self._public_url_generator
        if generator is None:
            raise DomainAliasServiceNotBound(
                "Domain alias generator is not bound. Bind a repository before use."
            )
        return generator

    def is_blocked_subdomain(self, subdomain: str) -> bool:
        return subdomain in RESERVED_SUBDOMAINS
