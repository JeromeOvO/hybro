from __future__ import annotations

import json
from typing import Any

from common.protocols import LLMStructuredGateway, MemoryRepository
from common.utils.logger import get_logger
from context_memory.config import ContextMemoryLLMConfig

logger = get_logger(__name__)


ROOM_SUMMARY_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "current_goal": {"type": ["string", "null"]},
        "key_decisions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "recent_agent_contributions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "important_constraints": {"type": "array", "items": {"type": "string"}},
        "room_facts": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "current_goal",
        "key_decisions",
        "open_questions",
        "recent_agent_contributions",
        "important_constraints",
        "room_facts",
    ],
    "additionalProperties": False,
}

_SUMMARY_FIELDS = (
    "current_goal",
    "key_decisions",
    "open_questions",
    "recent_agent_contributions",
    "important_constraints",
)


def build_summary_prompt(
    synthesis_text: str,
    existing_summary: dict[str, Any],
    existing_room_facts: list[dict[str, Any]],
) -> str:
    existing_projection = {
        field: existing_summary.get(field) for field in _SUMMARY_FIELDS
    }
    existing_projection["room_facts"] = [
        fact["content"]
        for fact in existing_room_facts
        if isinstance(fact, dict)
        and isinstance(fact.get("content"), str)
        and fact["content"].strip()
    ]
    return (
        "Extract incremental structured room summary fields from the synthesis. "
        "Return ONLY valid JSON with these keys:\n"
        '- "current_goal": string or null - what the user/room is trying to accomplish\n'
        '- "key_decisions": list of strings - decisions that should persist\n'
        '- "open_questions": list of strings - unresolved questions or blockers\n'
        '- "recent_agent_contributions": list of strings - last 3-5 agent result summaries\n'
        '- "important_constraints": list of strings - hard constraints stated\n'
        '- "room_facts": list of strings - durable facts worth remembering across sessions '
        "(e.g. user preferences, project names, deadlines, technical constraints).\n\n"
        "Merge rules applied after extraction:\n"
        "- current_goal: a new non-empty string replaces the existing value; null or "
        "an empty string preserves it.\n"
        "- key_decisions and important_constraints: new items are appended after "
        "existing items with case-insensitive deduplication; an empty list preserves "
        "existing items.\n"
        "- open_questions and recent_agent_contributions: a non-empty list replaces "
        "the existing list; an empty list preserves it.\n"
        "- room_facts: return only new durable facts; they are appended with "
        "case-insensitive deduplication, and an empty list preserves existing facts.\n"
        "No empty array clears an existing list.\n\n"
        "Existing projection:\n"
        f"{json.dumps(existing_projection, ensure_ascii=False)}\n\n"
        f"Synthesis:\n{synthesis_text}"
    )


def _non_empty_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _merge_case_insensitive(existing: Any, incremental: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*_non_empty_strings(existing), *_non_empty_strings(incremental)]:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _replace_when_non_empty(existing: Any, incremental: Any) -> list[str]:
    replacement = _non_empty_strings(incremental)
    return replacement or _non_empty_strings(existing)


async def update_room_summary(
    *,
    repository: MemoryRepository,
    llm_provider: LLMStructuredGateway,
    llm_config: ContextMemoryLLMConfig,
    room_id: str,
    synthesis_text: str,
    synthesis_turn_id: str | None,
    id_factory,
    now,
) -> bool:
    try:
        doc = await repository.get_room_summary_projection(room_id)
    except Exception:
        logger.exception(
            "Failed to load room summary projection",
            extra={"room_id": room_id},
        )
        return False
    if not doc:
        logger.warning(
            "Room summary projection missing",
            extra={"room_id": room_id},
        )
        return False

    existing = doc.get("room_summary") or {}
    existing_room_facts = doc.get("room_facts") or []
    try:
        response = await llm_provider.generate_structured(
            [
                {
                    "role": "system",
                    "content": "You extract structured information from text. Respond with valid JSON only.",
                },
                {
                    "role": "user",
                    "content": build_summary_prompt(
                        synthesis_text,
                        existing,
                        existing_room_facts,
                    ),
                },
            ],
            schema=ROOM_SUMMARY_EXTRACTION_SCHEMA,
            model=llm_config.summary_model,
        )
        extracted = getattr(response, "data", None)
    except Exception:
        logger.exception(
            "Failed to extract room summary",
            extra={"room_id": room_id},
        )
        return False
    if not isinstance(extracted, dict):
        logger.warning(
            "Room summary extraction returned invalid payload",
            extra={"room_id": room_id, "payload_type": type(extracted).__name__},
        )
        return False

    extracted_goal = extracted.get("current_goal")
    current_goal = (
        extracted_goal.strip()
        if isinstance(extracted_goal, str) and extracted_goal.strip()
        else existing.get("current_goal")
    )
    summary = {
        "current_goal": current_goal,
        "key_decisions": _merge_case_insensitive(
            existing.get("key_decisions"), extracted.get("key_decisions")
        ),
        "open_questions": _replace_when_non_empty(
            existing.get("open_questions"), extracted.get("open_questions")
        ),
        "recent_agent_contributions": _replace_when_non_empty(
            existing.get("recent_agent_contributions"),
            extracted.get("recent_agent_contributions"),
        ),
        "important_constraints": _merge_case_insensitive(
            existing.get("important_constraints"),
            extracted.get("important_constraints"),
        ),
        "last_updated_at": now(),
        "updated_after_turn_id": synthesis_turn_id
        or existing.get("updated_after_turn_id"),
    }

    existing_contents = {
        (fact.get("content") or "").casefold().strip()
        for fact in existing_room_facts
        if isinstance(fact, dict)
    }
    new_facts = []
    for fact_text in extracted.get("room_facts") or []:
        if not isinstance(fact_text, str) or not fact_text.strip():
            continue
        key = fact_text.casefold().strip()
        if key in existing_contents:
            continue
        new_facts.append(
            {
                "fact_id": id_factory(),
                "content": fact_text.strip(),
                "confidence": 1.0,
                "created_at": now(),
                "source_turn_id": synthesis_turn_id,
            }
        )
        existing_contents.add(key)

    try:
        persisted = await repository.update_room_summary_atomic(
            room_id,
            summary,
            new_facts=new_facts or None,
            max_facts=50,
        )
    except Exception:
        logger.exception(
            "Failed to persist room summary",
            extra={"room_id": room_id},
        )
        return False
    if not persisted:
        logger.warning(
            "Failed to persist room summary",
            extra={"room_id": room_id},
        )
    return persisted
