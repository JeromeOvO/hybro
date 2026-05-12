from __future__ import annotations

from collections.abc import Awaitable, Callable
import hashlib
import re

RESERVED_SUBDOMAINS = frozenset({
    "admin",
    "api",
    "www",
    "mail",
    "ftp",
    "app",
    "dashboard",
    "docs",
    "help",
    "support",
    "status",
    "blog",
    "cdn",
    "static",
    "assets",
    "auth",
    "login",
    "register",
    "account",
    "settings",
    "dev",
    "staging",
    "prod",
    "test",
    "demo",
    "sandbox",
})


class PublicUrlGenerator:
    def __init__(
        self,
        *,
        exists: Callable[[str, str], Awaitable[bool]],
        base_domain: str,
        protocol: str,
        id_factory: Callable[[], str],
    ) -> None:
        self._exists = exists
        self._base_domain = base_domain
        self._protocol = protocol
        self._id_factory = id_factory

    async def generate_public_url(
        self,
        *,
        agent_name: str | None,
        agent_id: str,
        preferred_subdomain: str | None = None,
    ) -> str:
        if preferred_subdomain:
            preferred = normalize_subdomain(preferred_subdomain)
            if preferred and await self._is_available(preferred):
                return self._url(preferred)

        base = normalize_subdomain(agent_name or "") or "agent"
        if await self._is_available(base):
            return self._url(base)

        hashed = f"{base}-{short_hash(agent_id)}"
        if await self._is_available(hashed):
            return self._url(hashed)

        fallback = str(self._id_factory())[:8]
        return self._url(f"{base}-{fallback}")

    async def _is_available(self, subdomain: str) -> bool:
        return subdomain not in RESERVED_SUBDOMAINS and not await self._exists(
            subdomain, self._base_domain
        )

    def _url(self, subdomain: str) -> str:
        return f"{self._protocol}://{subdomain}.{self._base_domain}"


def normalize_subdomain(value: str) -> str:
    normalized = value.lower()
    for word in ("the", "a", "an", "ai", "bot"):
        normalized = re.sub(rf"\b{word}\b", "", normalized)
    normalized = re.sub(r"[\s_]+", "", normalized)
    normalized = re.sub(r"[^a-z0-9-]", "", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized[:50]


def short_hash(value: str, length: int = 6) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:length]
