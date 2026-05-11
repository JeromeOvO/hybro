from __future__ import annotations

import os
from typing import Any

FILE_CAPABLE_EXACT = frozenset({"file", "*/*"})
FILE_CAPABLE_PREFIXES = frozenset({"image/", "audio/", "video/"})
FILE_CAPABLE_MIMES = frozenset({
    "application/pdf",
    "application/octet-stream",
    "application/zip",
    "application/x-tar",
    "application/gzip",
})


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


VECTOR_WEIGHT = _env_float("MATCH_VECTOR_WEIGHT", 0.85)
CAPABILITY_WEIGHT = _env_float("MATCH_CAPABILITY_WEIGHT", 0.15)
DEBATE_THRESHOLD = _env_float("MATCH_DEBATE_THRESHOLD", 0.3)
GAP_THRESHOLD = _env_float("MATCH_GAP_THRESHOLD", 0.15)
QUALITY_THRESHOLD = _env_float("MATCH_QUALITY_THRESHOLD", 0.4)


def supports_files(agent: dict[str, Any]) -> bool:
    card = agent.get("agent_card") or {}
    raw_modes = (
        card.get("default_input_modes")
        or card.get("defaultInputModes")
        or card.get("default_input_modes".lower())
        or ["text"]
    )
    modes = set(raw_modes)
    if modes & FILE_CAPABLE_EXACT:
        return True
    if modes & FILE_CAPABLE_MIMES:
        return True
    return any(
        any(mode.startswith(prefix) for prefix in FILE_CAPABLE_PREFIXES)
        for mode in modes
    )


def compute_capability_score(
    agent: dict[str, Any],
    required_input_modes: list[str] | None = None,
) -> float:
    if required_input_modes is None:
        return 1.0
    return 1.0 if supports_files(agent) else 0.0


def compute_final_score(
    vector_score: float,
    capability_score: float,
    *,
    vector_weight: float | None = None,
    capability_weight: float | None = None,
) -> float:
    resolved_vector_weight = (
        _env_float("MATCH_VECTOR_WEIGHT", VECTOR_WEIGHT)
        if vector_weight is None
        else vector_weight
    )
    resolved_capability_weight = (
        _env_float("MATCH_CAPABILITY_WEIGHT", CAPABILITY_WEIGHT)
        if capability_weight is None
        else capability_weight
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
        debate_threshold = _env_float("MATCH_DEBATE_THRESHOLD", DEBATE_THRESHOLD)
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
    gap_threshold = _env_float("MATCH_GAP_THRESHOLD", GAP_THRESHOLD)
    if (
        len(ranked) >= 2
        and (top["final_score"] - ranked[1]["final_score"]) > gap_threshold
    ):
        return [top]

    quality_threshold = _env_float("MATCH_QUALITY_THRESHOLD", QUALITY_THRESHOLD)
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
