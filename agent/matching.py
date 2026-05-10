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

VECTOR_WEIGHT = float(os.getenv("MATCH_VECTOR_WEIGHT", "0.85"))
CAPABILITY_WEIGHT = float(os.getenv("MATCH_CAPABILITY_WEIGHT", "0.15"))
DEBATE_THRESHOLD = float(os.getenv("MATCH_DEBATE_THRESHOLD", "0.3"))
GAP_THRESHOLD = float(os.getenv("MATCH_GAP_THRESHOLD", "0.15"))
QUALITY_THRESHOLD = float(os.getenv("MATCH_QUALITY_THRESHOLD", "0.4"))


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
    vector_weight: float = VECTOR_WEIGHT,
    capability_weight: float = CAPABILITY_WEIGHT,
) -> float:
    return vector_weight * vector_score + capability_weight * capability_score


def select_top_matches(
    ranked: list[dict[str, Any]],
    *,
    is_debate_mode: bool = False,
) -> list[dict[str, Any]]:
    if not ranked:
        return []

    if is_debate_mode:
        above_threshold = [
            match for match in ranked if match["final_score"] > DEBATE_THRESHOLD
        ]
        if not above_threshold:
            return ranked[: min(2, len(ranked))]
        count = min(max(len(above_threshold), 3), 5)
        if len(above_threshold) < 2 and len(ranked) >= 2:
            return ranked[:2]
        return above_threshold[:count] if len(above_threshold) >= 3 else above_threshold

    top = ranked[0]
    if (
        len(ranked) >= 2
        and (top["final_score"] - ranked[1]["final_score"]) > GAP_THRESHOLD
    ):
        return [top]

    qualified = [match for match in ranked if match["final_score"] > QUALITY_THRESHOLD]
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
