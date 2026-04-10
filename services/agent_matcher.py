"""
Agent Matching Pipeline

Deterministic agent matching without LLM calls. Two-stage pipeline:
  1. VectorSearch — Pinecone semantic similarity (full-sentence embedding)
  2. ScoreRanker — Composite ranking with I/O penalty and smart cutoff

Pinecone handles all semantic relevance via embeddings. The capability layer
only applies structural penalties (e.g. message has files but agent can't
handle them). This avoids duplicating semantic matching with naive token overlap.
"""

import os
from dataclasses import dataclass

from common.utils.logger import get_logger
from models.agent import Agent

logger = get_logger(__name__)

# File capability constants (mirrored from room_services.py lines 790-800)
FILE_CAPABLE_EXACT = frozenset({"file", "*/*"})
FILE_CAPABLE_PREFIXES = frozenset({"image/", "audio/", "video/"})
FILE_CAPABLE_MIMES = frozenset({
    "application/pdf",
    "application/octet-stream",
    "application/zip",
    "application/x-tar",
    "application/gzip",
})

# Scoring weights and thresholds (configurable via env vars)
VECTOR_WEIGHT = float(os.getenv("MATCH_VECTOR_WEIGHT", "0.85"))
CAPABILITY_WEIGHT = float(os.getenv("MATCH_CAPABILITY_WEIGHT", "0.15"))
DEBATE_THRESHOLD = float(os.getenv("MATCH_DEBATE_THRESHOLD", "0.3"))
GAP_THRESHOLD = float(os.getenv("MATCH_GAP_THRESHOLD", "0.15"))
QUALITY_THRESHOLD = float(os.getenv("MATCH_QUALITY_THRESHOLD", "0.4"))


def _agent_supports_files(agent: Agent) -> bool:
    """Does the agent support file input at all?

    Binary check mirroring _build_message_parts() supports_files (room_services.py:819-826).
    Checks both snake_case (default_input_modes) and camelCase (defaultInputModes) for
    backwards compatibility.
    """
    # Try snake_case first (Pydantic v2 style)
    agent_modes_raw = getattr(agent.agent_card, "default_input_modes", None)
    # Fall back to camelCase if not found
    if agent_modes_raw is None:
        agent_modes_raw = getattr(agent.agent_card, "defaultInputModes", None)

    agent_modes = set(agent_modes_raw or ["text"])

    if agent_modes & FILE_CAPABLE_EXACT:
        return True
    if agent_modes & FILE_CAPABLE_MIMES:
        return True
    return any(
        any(m.startswith(prefix) for prefix in FILE_CAPABLE_PREFIXES)
        for m in agent_modes
    )


def compute_capability_score(
    agent: Agent,
    required_input_modes: list[str] | None = None,
) -> float:
    """Structural capability score (0.0 or 1.0).

    Only checks I/O mode compatibility — whether the agent can handle
    the message's attachment types. Semantic relevance is handled entirely
    by Pinecone vector search.

    Args:
        agent: Agent to score
        required_input_modes: If present (non-None), message has attachments

    Returns:
        1.0 if compatible, 0.0 if incompatible (agent can't handle files)
    """
    if required_input_modes is None:
        # No attachments → all agents compatible
        return 1.0

    # Message has attachments — agent must support files
    return 1.0 if _agent_supports_files(agent) else 0.0


def select_top_agents(
    ranked: list["MatchedAgent"],
    is_debate_mode: bool,
) -> list["MatchedAgent"]:
    """Smart cutoff logic based on debate mode vs quality-driven selection.

    Args:
        ranked: Agents sorted by final_score descending
        is_debate_mode: If True, return 3-5 agents for diverse debate

    Returns:
        Filtered list of top agents
    """
    if not ranked:
        return []

    if is_debate_mode:
        # Debate mode: return 3-5 agents above threshold for diversity
        above_threshold = [a for a in ranked if a.final_score > DEBATE_THRESHOLD]
        if not above_threshold:
            # No agent scored well enough — return top 2 so debate is meaningful
            # (single-agent debate is pointless self-talk)
            return ranked[:min(2, len(ranked))]
        count = min(max(len(above_threshold), 3), 5)
        # Ensure at least 2 agents for meaningful debate
        if len(above_threshold) < 2 and len(ranked) >= 2:
            return ranked[:2]
        return above_threshold[:count] if len(above_threshold) >= 3 else above_threshold

    # Non-debate: quality-driven cutoff
    top = ranked[0]

    # If clear winner (large gap), return only top agent
    if len(ranked) >= 2 and (top.final_score - ranked[1].final_score) > GAP_THRESHOLD:
        return [top]

    # Otherwise return up to 3 agents above quality threshold
    qualified = [a for a in ranked if a.final_score > QUALITY_THRESHOLD]
    return qualified[:3] if qualified else [ranked[0]]


@dataclass
class MatchedAgent:
    """Agent with scoring breakdown."""
    agent: Agent
    vector_score: float
    capability_score: float     # 1.0 (compatible) or 0.0 (I/O mismatch)
    final_score: float


@dataclass
class MatchResult:
    """Result of agent matching pipeline."""
    agents: list[MatchedAgent]       # Sorted by final_score descending
    total_candidates: int            # How many candidates from vector search
    filtered_count: int              # How many passed capability filter


class AgentMatcher:
    """Deterministic agent matching pipeline. No LLM calls."""

    def __init__(self, database_service=None):
        from services.database_service import db_service
        from services.agent_capability_issue_service import capability_issue_service

        self._db = database_service or db_service
        self._capability_issue_service = capability_issue_service

    async def match(
        self,
        message_text: str,
        user_id: str | None = None,
        is_debate_mode: bool = False,
        required_input_modes: list[str] | None = None,
    ) -> MatchResult:
        """Match agents for a user message. Only used for 'all_agents' scope.

        Args:
            message_text: User message text
            user_id: Optional user ID for private agent visibility
            is_debate_mode: If True, returns 3-5 agents for debate diversity
            required_input_modes: If present, message has attachments (affects I/O scoring)

        Returns:
            MatchResult with sorted agents and metadata
        """
        # Exclude agents with repeated capability issues
        excluded = await self._capability_issue_service.get_excluded_agent_ids()

        # Stage 1: VectorSearch (Pinecone) — full-sentence semantic matching
        candidates = await self._db.query_similar_agents_with_scores(
            query_text=message_text,
            count=20,
            excluded_agent_ids=excluded,
            active_only=True,
            user_id=user_id,
        )
        total_candidates = len(candidates)

        if not candidates:
            logger.info("AgentMatcher: No candidates from vector search")
            return MatchResult(agents=[], total_candidates=0, filtered_count=0)

        # Stage 2: Apply I/O capability penalty and compute final scores
        scored: list[MatchedAgent] = []
        for agent, vector_score in candidates:
            cap_score = compute_capability_score(agent, required_input_modes)
            final = VECTOR_WEIGHT * vector_score + CAPABILITY_WEIGHT * cap_score
            scored.append(MatchedAgent(
                agent=agent,
                vector_score=vector_score,
                capability_score=cap_score,
                final_score=final,
            ))

        # Sort by final_score descending
        scored.sort(key=lambda m: m.final_score, reverse=True)

        # Debug: log top candidates with score breakdown
        for i, m in enumerate(scored[:6]):
            agent_name = m.agent.agent_card.name if m.agent.agent_card else m.agent.agent_id
            logger.info(
                "AgentMatcher rank #%d: %s — vector=%.3f, capability=%.3f, final=%.3f",
                i + 1, agent_name, m.vector_score, m.capability_score, m.final_score,
            )

        # Stage 3: ScoreRanker — smart cutoff
        selected = select_top_agents(scored, is_debate_mode)

        logger.info(
            "AgentMatcher: %d candidates → %d scored → %d selected (debate=%s)",
            total_candidates, len(scored), len(selected), is_debate_mode,
        )

        return MatchResult(
            agents=selected,
            total_candidates=total_candidates,
            filtered_count=len(scored),
        )
