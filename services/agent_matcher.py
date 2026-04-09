"""
Agent Matching Pipeline

Deterministic agent matching without LLM calls. Three-stage pipeline:
  1. VectorSearch — Pinecone similarity search with excluded agent filtering
  2. CapabilityFilter — Multi-dimensional capability scoring (skills, I/O modes)
  3. ScoreRanker — Composite ranking and smart cutoff (debate vs quality-driven)

Replaces the LLM-based analyze_message_routing() in agent_selection_service.py.
"""

import os
import re
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
VECTOR_WEIGHT = float(os.getenv("MATCH_VECTOR_WEIGHT", "0.6"))
CAPABILITY_WEIGHT = float(os.getenv("MATCH_CAPABILITY_WEIGHT", "0.4"))
DEBATE_THRESHOLD = float(os.getenv("MATCH_DEBATE_THRESHOLD", "0.3"))
GAP_THRESHOLD = float(os.getenv("MATCH_GAP_THRESHOLD", "0.15"))
QUALITY_THRESHOLD = float(os.getenv("MATCH_QUALITY_THRESHOLD", "0.4"))

# Simple tokenizer
_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Simple lowercase tokenizer for skill matching."""
    return set(_WORD_RE.findall(text.lower()))


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
    message_tokens: set[str],
    agent: Agent,
    required_input_modes: list[str] | None = None,
) -> float:
    """Score 0.0-1.0 based on how well agent capabilities match the message.

    Args:
        message_tokens: Tokenized message text (lowercase word set)
        agent: Agent to score
        required_input_modes: If present (non-None), message has attachments

    Returns:
        Capability score from 0.0 to 1.0
    """
    skills = agent.agent_card.skills or []

    # I/O mode: binary check (has attachments + agent supports files → 1.0, else → 0.0)
    if required_input_modes is not None:  # non-None = message has attachments
        io_score = 1.0 if _agent_supports_files(agent) else 0.0
    else:
        io_score = 1.0  # No attachments → no penalty

    if not skills:
        # General-purpose agents: baseline + I/O check
        return 0.3 * 0.85 + 0.15 * io_score

    best_skill_score = 0.0
    for skill in skills:
        name_tokens = _tokenize(skill.name)
        desc_tokens = _tokenize(skill.description or "")
        tags = set(t.lower() for t in (skill.tags or []))

        name_overlap = len(message_tokens & name_tokens) / max(len(name_tokens), 1)
        desc_overlap = len(message_tokens & desc_tokens) / max(len(desc_tokens), 1)
        tag_overlap = len(message_tokens & tags) / max(len(tags), 1)

        skill_score = (
            0.35 * name_overlap +
            0.25 * desc_overlap +
            0.25 * tag_overlap +
            0.15 * io_score
        )
        best_skill_score = max(best_skill_score, skill_score)

    return min(best_skill_score, 1.0)


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
        count = max(3, min(len(above_threshold), 5))
        return ranked[:count]

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
    vector_score: float         # 0.0 for non-vector scopes
    capability_score: float
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
        message_tokens = _tokenize(message_text)

        # Exclude agents with repeated capability issues
        excluded = await self._capability_issue_service.get_excluded_agent_ids()

        # Stage 1: VectorSearch (Pinecone)
        candidates = await self._db.query_similar_agents_with_scores(
            query_text=message_text,
            count=20,  # Request more candidates for filtering
            excluded_agent_ids=excluded,
            active_only=True,
            user_id=user_id,
        )
        total_candidates = len(candidates)

        if not candidates:
            logger.info("AgentMatcher: No candidates from vector search")
            return MatchResult(agents=[], total_candidates=0, filtered_count=0)

        # Stage 2: CapabilityFilter — score each candidate
        scored: list[MatchedAgent] = []
        for agent, vector_score in candidates:
            cap_score = compute_capability_score(
                message_tokens, agent, required_input_modes,
            )
            final = VECTOR_WEIGHT * vector_score + CAPABILITY_WEIGHT * cap_score
            scored.append(MatchedAgent(
                agent=agent,
                vector_score=vector_score,
                capability_score=cap_score,
                final_score=final,
            ))

        # Sort by final_score descending
        scored.sort(key=lambda m: m.final_score, reverse=True)

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
