"""
Agent Resolver Service

Encapsulates the common logic for finding an accessible agent:
1. Query candidates via vector similarity (active-only)
2. Optionally reorder by LLM selection
3. Real-time health probe with fallback to next candidate
4. In-memory TTL cache to avoid redundant probes

Design decisions:
- Read-only: never marks agents inactive. Status transitions are the
  exclusive responsibility of the background AgentHealthService.
- Uses a short 3-second timeout for on-demand probes (vs 10s background).
- Respects settings.agent_health_check_enabled; skips probes when disabled.
"""

from dataclasses import dataclass, field
from time import monotonic

import httpx
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    PREV_AGENT_CARD_WELL_KNOWN_PATH,
)

from common.utils.logger import get_logger
from config.settings import settings
from models.agent import Agent, AgentStatus
from services.database_service import db_service
from services.openai_service import openai_service

logger = get_logger(__name__)

# On-demand probe timeout (seconds). Kept short to avoid blocking the
# critical path. The background AgentHealthService uses a longer 10s timeout.
_PROBE_TIMEOUT: float = 3.0

# How long a cached health result is considered fresh (seconds).
_CACHE_TTL: float = 30.0


# ---------------------------------------------------------------------------
# Health probe cache
# ---------------------------------------------------------------------------


class _HealthCache:
    """Simple in-memory TTL cache for agent health probe results."""

    def __init__(self, ttl: float = _CACHE_TTL):
        self._ttl = ttl
        self._entries: dict[str, tuple[bool, float]] = {}

    def get(self, agent_id: str) -> bool | None:
        """Return cached health status, or None if missing / expired."""
        entry = self._entries.get(agent_id)
        if entry is not None:
            is_healthy, timestamp = entry
            if (monotonic() - timestamp) < self._ttl:
                return is_healthy
            # Expired – remove stale entry
            del self._entries[agent_id]
        return None

    def set(self, agent_id: str, is_healthy: bool) -> None:
        self._entries[agent_id] = (is_healthy, monotonic())


# ---------------------------------------------------------------------------
# Resolve result
# ---------------------------------------------------------------------------


@dataclass
class ResolveResult:
    """Result of agent resolution."""

    agent: Agent | None
    tried_agents: list[str] = field(default_factory=list)
    failure_reason: str | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AgentResolverService:
    """Resolves the best accessible agent for a given task description.

    Combines vector similarity search, optional LLM-based ranking, and
    real-time health probing into a single ``resolve()`` call that both
    RoomMessageCenter and WorkflowCenter can share.
    """

    def __init__(self) -> None:
        self.database_service = db_service
        self.openai_service = openai_service
        self._health_cache = _HealthCache()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def resolve(
        self,
        query_text: str,
        *,
        allowed_agent_ids: list[str] | None = None,
        count: int = 5,
        use_llm_selection: bool = False,
        user_id: str | None = None,
    ) -> ResolveResult:
        """Find the best accessible agent for *query_text*.

        Args:
            query_text: Task description or user input for similarity search.
            allowed_agent_ids: Optional whitelist of agent IDs (room scoping).
            count: Max candidates to fetch from vector search.
            use_llm_selection: If ``True``, use LLM to pick the best agent
                from candidates (workflow-style).  If ``False``, use the top
                vector-similarity match (room-style).
            user_id: Optional user ID for visibility filtering.

        Returns:
            A :class:`ResolveResult` containing the chosen agent or a
            human-readable ``failure_reason`` when no agent is available.
        """
        # Server-side enforcement: sanitize allowed IDs before Pinecone query.
        # Ensures only active + visible (public or owned by user) agents are
        # included, regardless of what the caller passes.
        allowed_agent_ids = await self._sanitize_allowed_ids(
            allowed_agent_ids, user_id
        )

        # Caller scoped to specific agents but none survived sanitization.
        if allowed_agent_ids is not None and len(allowed_agent_ids) == 0:
            return ResolveResult(
                agent=None,
                tried_agents=[],
                failure_reason=(
                    "None of the specified agents are currently available "
                    "(they may be inactive or not accessible to you)."
                ),
            )

        # Step 1 – vector similarity search (already filters active_only)
        candidates = await self.database_service.query_similar_agents(
            query_text,
            count=count,
            allowed_agent_ids=allowed_agent_ids,
            active_only=True,
            user_id=user_id,
        )

        if not candidates:
            return ResolveResult(
                agent=None,
                tried_agents=[],
                failure_reason="No active agents matched the query.",
            )

        # Step 2 – optionally reorder by LLM preference
        if use_llm_selection and len(candidates) > 1:
            candidates = await self._reorder_by_llm(query_text, candidates)

        # Step 3 – health probe with fallback (if enabled)
        if settings.agent_health_check_enabled:
            return await self._pick_first_healthy(candidates)

        # Health checks disabled – return top candidate directly
        top = candidates[0]
        return ResolveResult(
            agent=top,
            tried_agents=[top.agent_card.name],
            failure_reason=None,
        )

    # ------------------------------------------------------------------
    # Allowed-ID sanitization
    # ------------------------------------------------------------------

    async def _sanitize_allowed_ids(
        self,
        allowed_agent_ids: list[str] | None,
        user_id: str | None,
    ) -> list[str] | None:
        """Server-side filter: keep only active + visible agent IDs.

        Ensures ``allowed_agent_ids`` only contains agents that are:

        * **Active** (``agent_status == 'active'``)
        * **Visible** to *user_id* — either public (``is_public`` is True or
          missing) or private but owned by *user_id*
          (``provider_id == user_id``).

        Returns:
            ``None`` when no scoping was requested (input was ``None``).
            A (possibly empty) list of sanitized IDs otherwise.  An empty
            list signals that the caller scoped to agents but none survived,
            so ``resolve()`` should fail fast rather than fall back to an
            unrestricted search.
        """
        if allowed_agent_ids is None:
            return None  # No scoping requested — keep it that way

        if not allowed_agent_ids:
            return []  # Caller passed empty list — stays empty

        query = {
            "$and": [
                {"agent_id": {"$in": [str(aid) for aid in allowed_agent_ids]}},
                {"agent_status": AgentStatus.active.value},
            ]
        }
        # Uses the public method which internally applies visibility filter
        agents = await self.database_service.get_agents_with_conditions_visible(
            user_id=user_id,
            query=query,
        )

        sanitized = [a.agent_id for a in agents]

        if len(sanitized) < len(allowed_agent_ids):
            dropped = set(str(aid) for aid in allowed_agent_ids) - set(sanitized)
            logger.info(
                "AgentResolver: Sanitized allowed_agent_ids — dropped %d IDs "
                "(inactive or not visible to user %s): %s",
                len(dropped),
                user_id,
                dropped,
            )

        return sanitized

    # ------------------------------------------------------------------
    # LLM reordering
    # ------------------------------------------------------------------

    async def _reorder_by_llm(
        self, query_text: str, candidates: list[Agent]
    ) -> list[Agent]:
        """Ask the LLM to pick the best agent; move it to the front."""
        try:
            best_agent_id = await self.openai_service.select_best_agent_for_task(
                query_text, candidates
            )
            best = next(
                (a for a in candidates if a.agent_id == best_agent_id), None
            )
            if best is not None and best.agent_status == AgentStatus.active:
                others = [a for a in candidates if a.agent_id != best_agent_id]
                return [best, *others]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "AgentResolver: LLM selection failed, using vector order: %s",
                exc,
            )
        return candidates

    # ------------------------------------------------------------------
    # Health probing
    # ------------------------------------------------------------------

    async def _pick_first_healthy(
        self, candidates: list[Agent]
    ) -> ResolveResult:
        """Return the first candidate that passes a real-time health probe.

        Uses an in-memory TTL cache to avoid redundant HTTP calls.
        Does *not* mutate agent status in the database.
        """
        tried: list[str] = []

        for candidate in candidates:
            name = candidate.agent_card.name
            tried.append(name)

            # Check cache first
            cached = self._health_cache.get(candidate.agent_id)
            if cached is True:
                logger.debug(
                    "AgentResolver: Agent %s (%s) healthy (cached)",
                    candidate.agent_id,
                    name,
                )
                return ResolveResult(
                    agent=candidate,
                    tried_agents=tried,
                    failure_reason=None,
                )
            if cached is False:
                logger.debug(
                    "AgentResolver: Agent %s (%s) unhealthy (cached), skipping",
                    candidate.agent_id,
                    name,
                )
                continue

            # Cache miss – do a live probe
            is_healthy = await self._probe_agent(candidate)
            self._health_cache.set(candidate.agent_id, is_healthy)

            if is_healthy:
                return ResolveResult(
                    agent=candidate,
                    tried_agents=tried,
                    failure_reason=None,
                )

            logger.warning(
                "AgentResolver: Agent %s (%s) is unreachable, trying next candidate",
                candidate.agent_id,
                name,
            )

        # All candidates exhausted
        return ResolveResult(
            agent=None,
            tried_agents=tried,
            failure_reason=(
                f"All matched agents are currently unreachable: "
                f"{', '.join(tried)}. They may be offline or experiencing "
                f"issues. Please try again later."
            ),
        )

    @staticmethod
    async def _probe_agent(agent: Agent) -> bool:
        """Lightweight HTTP probe to check if an agent is reachable.

        Uses a short timeout (3 s) to keep the critical path fast.
        """
        agent_url = agent.agent_card.url
        try:
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
                card_url = agent_url.rstrip("/") + AGENT_CARD_WELL_KNOWN_PATH
                response = await client.get(card_url)

                # Fall back to previous well-known path if not found
                if response.status_code == 404:
                    card_url = (
                        agent_url.rstrip("/") + PREV_AGENT_CARD_WELL_KNOWN_PATH
                    )
                    response = await client.get(card_url)

                return response.status_code < 400

        except httpx.TimeoutException:
            logger.debug(
                "AgentResolver: Probe timed out for agent %s (%s)",
                agent.agent_id,
                agent.agent_card.name,
            )
            return False
        except httpx.RequestError as exc:
            logger.debug(
                "AgentResolver: Probe failed for agent %s (%s): %s",
                agent.agent_id,
                agent.agent_card.name,
                exc,
            )
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "AgentResolver: Unexpected error probing agent %s: %s",
                agent.agent_id,
                exc,
            )
            return False


# Singleton instance
agent_resolver_service = AgentResolverService()
