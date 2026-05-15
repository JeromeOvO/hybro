from __future__ import annotations

from typing import Any

from common.protocols import LLMProvider, MemoryRepository

from context_memory.config import ContextMemoryLLMConfig


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


def build_summary_prompt(synthesis_text: str) -> str:
    return (
        "Extract structured room summary fields from the following synthesis. "
        "Return ONLY valid JSON with these keys:\n"
        '- "current_goal": string or null - what the user/room is trying to accomplish\n'
        '- "key_decisions": list of strings - decisions that should persist\n'
        '- "open_questions": list of strings - unresolved questions or blockers\n'
        '- "recent_agent_contributions": list of strings - last 3-5 agent result summaries\n'
        '- "important_constraints": list of strings - hard constraints stated\n'
        '- "room_facts": list of strings - durable facts worth remembering across sessions '
        "(e.g. user preferences, project names, deadlines, technical constraints). "
        "Only include facts NOT already obvious from the goal or decisions. "
        "Return an empty list if there are no new facts.\n\n"
        f"Synthesis:\n{synthesis_text}"
    )


async def update_room_summary(
    *,
    repository: MemoryRepository,
    llm_provider: LLMProvider,
    llm_config: ContextMemoryLLMConfig,
    room_id: str,
    synthesis_text: str,
    synthesis_turn_id: str | None,
    id_factory,
    now,
) -> bool:
    try:
        response = await llm_provider.generate_structured(
            [
                {
                    "role": "system",
                    "content": "You extract structured information from text. Respond with valid JSON only.",
                },
                {"role": "user", "content": build_summary_prompt(synthesis_text)},
            ],
            schema=ROOM_SUMMARY_EXTRACTION_SCHEMA,
            model=llm_config.summary_model,
        )
        extracted = getattr(response, "data", None)
    except Exception:
        return False
    if not isinstance(extracted, dict):
        return False

    doc = await repository.get_room_summary_projection(room_id)
    if not doc:
        return False
    existing = doc.get("room_summary") or {}
    summary = {
        "current_goal": extracted.get("current_goal")
        if extracted.get("current_goal") is not None
        else existing.get("current_goal"),
        "key_decisions": extracted.get("key_decisions")
        if extracted.get("key_decisions") is not None
        else existing.get("key_decisions", []),
        "open_questions": extracted.get("open_questions")
        if extracted.get("open_questions") is not None
        else existing.get("open_questions", []),
        "recent_agent_contributions": extracted.get("recent_agent_contributions")
        if extracted.get("recent_agent_contributions") is not None
        else existing.get("recent_agent_contributions", []),
        "important_constraints": extracted.get("important_constraints")
        if extracted.get("important_constraints") is not None
        else existing.get("important_constraints", []),
        "last_updated_at": now(),
        "updated_after_turn_id": synthesis_turn_id or existing.get("updated_after_turn_id"),
    }

    existing_contents = {
        (fact.get("content") or "").lower().strip()
        for fact in (doc.get("room_facts") or [])
        if isinstance(fact, dict)
    }
    new_facts = []
    for fact_text in extracted.get("room_facts") or []:
        if not isinstance(fact_text, str) or not fact_text.strip():
            continue
        key = fact_text.lower().strip()
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

    return await repository.update_room_summary_atomic(
        room_id,
        summary,
        new_facts=new_facts or None,
        max_facts=50,
    )
