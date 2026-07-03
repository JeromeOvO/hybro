from __future__ import annotations

from typing import Any

from common.config import settings
from common.utils.a2a_file_modes import (
    agent_accepts_required_input_modes,
    agent_supports_any_file,
)


VECTOR_WEIGHT = settings.match_vector_weight
CAPABILITY_WEIGHT = settings.match_capability_weight
DEBATE_THRESHOLD = settings.match_debate_threshold
GAP_THRESHOLD = settings.match_gap_threshold
QUALITY_THRESHOLD = settings.match_quality_threshold


def supports_files(agent: dict[str, Any]) -> bool:
    card = agent.get("agent_card") or {}
    return agent_supports_any_file(card)


def compute_capability_score(
    agent: dict[str, Any],
    required_input_modes: list[str] | None = None,
) -> float:
    card = agent.get("agent_card") or {}
    if agent_accepts_required_input_modes(card, required_input_modes):
        return 1.0
    return 0.0


def compute_final_score(
    vector_score: float,
    capability_score: float,
    *,
    vector_weight: float | None = None,
    capability_weight: float | None = None,
) -> float:
    resolved_vector_weight = (
        VECTOR_WEIGHT if vector_weight is None else vector_weight
    )
    resolved_capability_weight = (
        CAPABILITY_WEIGHT if capability_weight is None else capability_weight
    )
    return (
        resolved_vector_weight * vector_score
        + resolved_capability_weight * capability_score
    )


def select_top_matches(
    ranked: list[dict[str, Any]],
    *,
    is_debate_mode: bool = False,
) -> list[dict[str, Any]]:
    if not ranked:
        return []

    if is_debate_mode:
        debate_threshold = DEBATE_THRESHOLD
        above_threshold = [
            match for match in ranked if match["final_score"] > debate_threshold
        ]
        if not above_threshold:
            return ranked[: min(2, len(ranked))]
        count = min(max(len(above_threshold), 3), 5)
        if len(above_threshold) < 2 and len(ranked) >= 2:
            return ranked[:2]
        return above_threshold[:count] if len(above_threshold) >= 3 else above_threshold

    top = ranked[0]
    gap_threshold = GAP_THRESHOLD
    if (
        len(ranked) >= 2
        and (top["final_score"] - ranked[1]["final_score"]) > gap_threshold
    ):
        return [top]

    quality_threshold = QUALITY_THRESHOLD
    qualified = [match for match in ranked if match["final_score"] > quality_threshold]
    return qualified[:3] if qualified else [top]


def rank_agent_docs(
    docs: list[dict[str, Any]],
    vector_scores: dict[str, float],
    *,
    required_input_modes: list[str] | None = None,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for doc in docs:
        vector_score = vector_scores.get(doc.get("agent_id"), 0.0)
        capability_score = compute_capability_score(doc, required_input_modes)
        ranked.append(
            {
                "agent_id": doc.get("agent_id"),
                "agent": doc,
                "vector_score": vector_score,
                "capability_score": capability_score,
                "final_score": compute_final_score(vector_score, capability_score),
            }
        )
    ranked.sort(key=lambda match: match["final_score"], reverse=True)
    return ranked
