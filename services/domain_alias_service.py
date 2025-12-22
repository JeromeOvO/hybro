"""
Domain Alias Service

Provides URL masking for A2A agents by generating unique subdomains.

Example:
    Real URL:   http://13.57.247.41:20004
    Public URL: https://storyagent.hybro.ai
"""

import hashlib
import re
from uuid import uuid4

from common.utils.logger import get_logger
from database.mongodb import get_db

logger = get_logger(__name__)


class DomainAliasService:
    """Service for generating unique subdomains for agent URLs."""

    # Hardcoded domain masking configuration
    BASE_DOMAIN = "hybro.ai"
    PROTOCOL = "https"

    # Subdomains that are blocked/reserved for system use
    BLOCKED_SUBDOMAINS = frozenset({
        "admin", "api", "www", "mail", "ftp", "app", "dashboard",
        "docs", "help", "support", "status", "blog", "cdn", "static",
        "assets", "auth", "login", "register", "account", "settings",
        "dev", "staging", "prod", "test", "demo", "sandbox",
    })

    def __init__(self):
        pass

    @property
    def base_domain(self) -> str:
        """Get base domain."""
        return self.BASE_DOMAIN

    @property
    def protocol(self) -> str:
        """Get protocol."""
        return self.PROTOCOL

    def _normalize_name(self, name: str) -> str:
        """
        Normalize agent name into a valid subdomain component.
        
        - Lowercase
        - Remove common filler words
        - Remove spaces and special characters
        - Limit length to 50 chars (DNS subdomain limit is 63)
        """
        if not name:
            return ""

        # Convert to lowercase
        normalized = name.lower()

        # Remove common words that don't add value
        stop_words = ["the", "a", "an", "ai", "bot"]
        for word in stop_words:
            normalized = re.sub(rf"\b{word}\b", "", normalized)

        # Replace spaces and underscores with empty string (create compact name)
        normalized = re.sub(r"[\s_]+", "", normalized)

        # Remove non-alphanumeric characters except hyphens
        normalized = re.sub(r"[^a-z0-9-]", "", normalized)

        # Remove consecutive hyphens
        normalized = re.sub(r"-+", "-", normalized)

        # Trim hyphens from start/end
        normalized = normalized.strip("-")

        # Limit to 50 chars (DNS subdomain limit is 63)
        return normalized[:50]

    def _generate_short_hash(self, input_str: str, length: int = 6) -> str:
        """Generate a short hash for uniqueness."""
        return hashlib.sha256(input_str.encode()).hexdigest()[:length]

    async def _is_subdomain_available(self, subdomain: str) -> bool:
        """Check if a subdomain is already in use or blocked."""
        # Check against blocked list
        if subdomain in self.BLOCKED_SUBDOMAINS:
            return False

        # Check if any agent already uses this subdomain
        db = await get_db()
        existing = await db.agents.find_one({
            "public_url": {"$regex": f"://{subdomain}\\.{self.BASE_DOMAIN}"}
        })
        return existing is None

    async def generate_unique_subdomain(
        self,
        agent_name: str,
        agent_id: str,
        preferred_subdomain: str | None = None
    ) -> str:
        """
        Generate a unique subdomain for an agent.
        
        Strategy:
        1. If preferred subdomain provided and available, use it
        2. Try normalized agent name (e.g., "Story Agent" -> "story")
        3. If taken, append short hash based on agent_id
        4. If still taken, use UUID-based fallback (guaranteed unique)
        """
        # Strategy 0: If user specified a custom subdomain, validate and use it
        if preferred_subdomain:
            normalized_preferred = self._normalize_name(preferred_subdomain)
            if normalized_preferred and await self._is_subdomain_available(normalized_preferred):
                return normalized_preferred

        # Strategy 1: Try normalized name
        base_subdomain = self._normalize_name(agent_name)
        if not base_subdomain:
            base_subdomain = "agent"

        if await self._is_subdomain_available(base_subdomain):
            return base_subdomain

        # Strategy 2: Append short hash based on agent_id
        hash_suffix = self._generate_short_hash(agent_id, 6)
        subdomain_with_hash = f"{base_subdomain}-{hash_suffix}"

        if await self._is_subdomain_available(subdomain_with_hash):
            return subdomain_with_hash

        # Strategy 3: Use UUID-based subdomain (guaranteed unique)
        unique_suffix = str(uuid4())[:8]
        return f"{base_subdomain}-{unique_suffix}"

    async def generate_public_url(
        self,
        agent_name: str,
        agent_id: str,
        preferred_subdomain: str | None = None
    ) -> str:
        """
        Generate a public (masked) URL for an agent.
        
        Args:
            agent_name: The agent's display name
            agent_id: The agent's unique ID
            preferred_subdomain: Optional user-specified subdomain
            
        Returns:
            Public URL like https://storyagent.hybro.ai
        """
        subdomain = await self.generate_unique_subdomain(
            agent_name, agent_id, preferred_subdomain
        )
        return f"{self.PROTOCOL}://{subdomain}.{self.BASE_DOMAIN}"

    async def get_public_url_by_agent_id(self, agent_id: str) -> str | None:
        """
        Get the public (masked) URL for an agent by agent_id.
        
        Args:
            agent_id: The agent's unique identifier
            
        Returns:
            The public URL (e.g., "https://storyagent.hybro.ai") or None if not found
        """
        db = await get_db()
        agent = await db.agents.find_one({"agent_id": agent_id})
        
        if agent:
            return agent.get("public_url")
        return None

    def is_masked_url(self, url: str) -> bool:
        """
        Check if a URL is a Hybro masked URL.
        """
        if not url:
            return False

        url_lower = url.lower()
        base_domain = self.base_domain.lower()

        # Check if URL contains the base domain
        if base_domain not in url_lower:
            return False

        # Exclude main platform URLs (api.hybro.ai, www.hybro.ai)
        excluded_prefixes = [
            f"https://api.{base_domain}",
            f"http://api.{base_domain}",
            f"https://www.{base_domain}",
            f"http://www.{base_domain}",
            f"https://{base_domain}",
            f"http://{base_domain}",
        ]

        for prefix in excluded_prefixes:
            if url_lower.startswith(prefix):
                return False

        return True


# Singleton instance
domain_alias_service = DomainAliasService()
