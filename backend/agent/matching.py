from __future__ import annotations

import re
import unicodedata
from typing import Any

from common.utils.a2a_file_modes import (
    agent_accepts_required_input_modes,
    agent_supports_any_file,
)

FALLBACK_HIT_THRESHOLD = 0.25

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def supports_files(agent: dict[str, Any]) -> bool:
    card = agent.get("agent_card") or {}
    return agent_supports_any_file(card)


def accepts_input_modes(
    agent: dict[str, Any],
    required_input_modes: list[str] | None = None,
) -> bool:
    return agent_accepts_required_input_modes(
        agent.get("agent_card") or {},
        required_input_modes,
    )


def normalize_search_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(normalized.split())


def is_searchable_query(query: str) -> bool:
    normalized = normalize_search_text(query)
    return bool(_latin_tokens(normalized) or _CJK_RE.search(normalized))


def lexical_fallback_score(query: str, agent: dict[str, Any]) -> float:
    normalized_query = normalize_search_text(query)
    if not is_searchable_query(normalized_query):
        return 0.0

    card = agent.get("agent_card") or {}
    fields: list[tuple[float, Any]] = [
        (1.0, card.get("name") or agent.get("name")),
        (0.7, card.get("description") or agent.get("description")),
    ]
    for skill in card.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        fields.extend(
            [
                (0.9, skill.get("name")),
                (0.8, " ".join(str(tag) for tag in skill.get("tags") or [])),
                (0.7, skill.get("description")),
            ]
        )
    return max(
        (
            multiplier * _field_match_score(normalized_query, normalize_search_text(text))
            for multiplier, text in fields
            if text
        ),
        default=0.0,
    )


def rank_agent_docs(
    docs: list[dict[str, Any]],
    mongo_scores: dict[str, float],
    *,
    mongo_matched_ids: set[str] | frozenset[str] = frozenset(),
    query: str,
) -> list[dict[str, Any]]:
    max_mongo_score = max(mongo_scores.values(), default=0.0)
    ranked: list[dict[str, Any]] = []
    for doc in docs:
        agent_id = str(doc.get("agent_id") or "")
        raw_mongo_score = max(0.0, float(mongo_scores.get(agent_id, 0.0)))
        normalized_mongo_score = (
            raw_mongo_score / max_mongo_score if max_mongo_score > 0 else 0.0
        )
        fallback_score = lexical_fallback_score(query, doc)
        if (
            agent_id not in mongo_matched_ids
            and fallback_score < FALLBACK_HIT_THRESHOLD
        ):
            continue
        lexical_score = max(normalized_mongo_score, fallback_score)
        ranked.append(
            {
                "agent_id": agent_id,
                "agent": doc,
                "lexical_score": lexical_score,
                "final_score": lexical_score,
            }
        )
    ranked.sort(key=lambda match: (-match["lexical_score"], match["agent_id"]))
    return ranked


def select_top_matches(
    ranked: list[dict[str, Any]],
    *,
    is_debate_mode: bool = False,
    limit: int = 5,
) -> list[dict[str, Any]]:
    effective_limit = min(max(0, limit), 5) if is_debate_mode else max(0, limit)
    return ranked[:effective_limit]


def _field_match_score(query: str, field: str) -> float:
    if not field:
        return 0.0
    exact = 1.0 if query in field else 0.0
    query_tokens = set(_latin_tokens(query))
    field_tokens = set(_latin_tokens(field))
    token_recall = (
        len(query_tokens & field_tokens) / len(query_tokens) if query_tokens else 0.0
    )
    query_grams = _cjk_grams(query)
    field_grams = _cjk_grams(field)
    gram_recall = (
        len(query_grams & field_grams) / len(query_grams) if query_grams else 0.0
    )
    return max(exact, token_recall, gram_recall)


def _cjk_grams(value: str) -> set[str]:
    chars = _CJK_RE.findall(value)
    if len(chars) < 2:
        return set(chars)
    return {"".join(chars[index : index + 2]) for index in range(len(chars) - 1)}


def _latin_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    for char in value:
        is_latin = char.isalpha() and "LATIN" in unicodedata.name(char, "")
        if char.isdecimal() or is_latin:
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens
