from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from common.protocols import LLMProvider, MemoryRepository, RoomHistoryReader
from common.utils.context_utils import (
    LLM_TURN_NOTES_THRESHOLD,
    MAX_HISTORY_TURNS,
    MAX_SUMMARY_CHARS,
    clean_mention_format,
    estimate_tokens,
    extract_turn_notes,
)

from context_memory.config import ContextMemoryLLMConfig


TURN_NOTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "keywords": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "one_liner": {"type": "string"},
    },
    "required": ["keywords", "entities", "tags", "one_liner"],
    "additionalProperties": False,
}


def build_turn_content(message_text: str, attachments: list[Any] | None = None) -> str:
    if not attachments:
        return message_text
    parts = [message_text] if message_text else []
    rendered = []
    for attachment in attachments:
        if isinstance(attachment, dict):
            name = attachment.get("file_name") or attachment.get("name") or "attachment"
            url = attachment.get("url") or attachment.get("file_url") or attachment.get("s3_key") or ""
        else:
            name = getattr(attachment, "file_name", None) or getattr(attachment, "name", None) or "attachment"
            url = getattr(attachment, "url", None) or getattr(attachment, "file_url", None) or getattr(attachment, "s3_key", None) or ""
        rendered.append(f"- {name}: {url}" if url else f"- {name}")
    if rendered:
        parts.append("[Attachments]")
        parts.extend(rendered)
    return "\n".join(parts)


async def project_message_from_history(
    *,
    room_id: str,
    message_id: str,
    repository: MemoryRepository,
    room_history_reader: RoomHistoryReader,
    id_factory: Callable[[], str],
    now,
) -> dict:
    messages = await room_history_reader.get_messages_by_ids([message_id])
    message = next((m for m in messages if getattr(m, "message_id", None) == message_id), None)
    if message is None:
        return {"projected": False, "reason": "missing_message"}
    if message.room_id != room_id:
        return {"projected": False, "reason": "room_mismatch"}

    message_type = getattr(message, "message_type", "user")
    content_text = extract_message_text(getattr(message, "content", None))
    if not content_text:
        return {"projected": False, "reason": "empty_content"}

    if message_type == "user":
        defaults = new_room_memory_doc(
            room_id=room_id,
            memory_id=id_factory(),
            now=now(),
        )
        await repository.ensure_room_memory(room_id, defaults)
        turn = user_turn(
            message_id=message_id,
            content=content_text,
            user_id=getattr(message, "sender_id", None),
            timestamp=getattr(message, "created_at", None) or now(),
        )
    else:
        if not await repository.get_room_memory(room_id):
            return {"projected": False, "reason": "missing_room_memory"}
        turn = agent_turn(
            content=content_text,
            agent_id=getattr(message, "agent_id", None) or getattr(message, "sender_id", None),
            agent_name=getattr(message, "sender_name", None),
            timestamp=getattr(message, "created_at", None) or now(),
            turn_id=f"message:{message_id}",
        )

    modified, matched, already_exists = await repository.push_and_trim_conversation_turn_if_absent(
        room_id,
        turn,
        turn_id=turn["turn_id"],
        max_turns=MAX_HISTORY_TURNS,
        summary_stub=f"[{turn.get('agent_name') or turn['role'].title()}] {content_text[:200]}...",
        max_summary_chars=MAX_SUMMARY_CHARS,
    )
    if already_exists:
        return {"projected": False, "reason": "duplicate"}
    if not matched:
        return {"projected": False, "reason": "missing_room_memory"}
    return {"projected": bool(modified), "reason": "projected"}


def new_room_memory_doc(*, room_id: str, memory_id: str, now) -> dict:
    return {
        "room_id": room_id,
        "memory_id": memory_id,
        "memory_content": {"summary": None, "conversation_history": []},
        "conversation_history": [],
        "room_summary": {
            "current_goal": None,
            "key_decisions": [],
            "open_questions": [],
            "recent_agent_contributions": [],
            "important_constraints": [],
        },
        "room_facts": [],
        "memory_created_at": now,
        "last_activity_at": now,
        "total_messages": 0,
        "total_compactions": 0,
    }


def user_turn(*, message_id: str, content: str, user_id: str | None, timestamp) -> dict:
    return {
        "turn_id": f"message:{message_id}",
        "role": "user",
        "user_id": user_id,
        "timestamp": timestamp,
        "representation": "full",
        "content": content,
        "content_type": "text",
        "turn_type": "message",
        "estimated_tokens_full": estimate_tokens(content),
        "estimated_tokens_compact": 20,
        "turn_notes": extract_turn_notes(content),
    }


def agent_turn(
    *,
    content: str,
    agent_id: str | None,
    agent_name: str | None,
    timestamp,
    turn_id: str | None = None,
    was_successful: bool | None = None,
) -> dict:
    return {
        "turn_id": turn_id or "",
        "role": "agent",
        "agent_id": agent_id,
        "agent_name": agent_name,
        "timestamp": timestamp,
        "representation": "full",
        "content": content,
        "content_type": "agent_response",
        "turn_type": "message",
        "estimated_tokens_full": estimate_tokens(content),
        "estimated_tokens_compact": 20,
        "turn_notes": extract_turn_notes(content),
        "was_successful": was_successful,
    }


def supervisor_turn(*, turn_id: str, content: str, timestamp) -> dict:
    return {
        "turn_id": turn_id,
        "role": "supervisor",
        "timestamp": timestamp,
        "representation": "full",
        "content": content,
        "content_type": "text",
        "turn_type": "message",
        "estimated_tokens_full": estimate_tokens(content),
        "estimated_tokens_compact": 20,
        "turn_notes": extract_turn_notes(content),
    }


async def initialize_or_update_room_memory(
    *,
    repository: MemoryRepository,
    room_id: str,
    memory_content: str | None,
    room_agent_set: dict | None,
    user_id: str | None,
    attachments: list | None,
    id_factory: Callable[[], str],
    now,
) -> dict | None:
    room_memory = await repository.get_room_memory(room_id)
    if not room_memory:
        room_memory = new_room_memory_doc(
            room_id=room_id,
            memory_id=id_factory(),
            now=now(),
        )
        await repository.create_room_memory(room_memory)

    if memory_content:
        clean_message = clean_mention_format(memory_content, room_agent_set or {})
        content = build_turn_content(clean_message, attachments)
        turn = user_turn(
            message_id=id_factory(),
            content=content,
            user_id=user_id,
            timestamp=now(),
        )
        turn["turn_id"] = id_factory()
        modified, matched = await repository.push_and_trim_conversation_turn(
            room_id,
            turn,
            max_turns=MAX_HISTORY_TURNS,
            summary_stub=f"[User] {clean_message[:200]}...",
            max_summary_chars=MAX_SUMMARY_CHARS,
        )
        if not modified and not matched:
            return None
        room_memory = await repository.get_room_memory(room_id) or room_memory
    return room_memory


async def add_agent_response_to_memory(
    *,
    repository: MemoryRepository,
    room_id: str,
    agent_id: str,
    agent_name: str,
    response_text: str,
    was_successful: bool,
    id_factory: Callable[[], str],
    now,
    llm_provider: LLMProvider,
    llm_config: ContextMemoryLLMConfig,
    background_task_runner: Callable[[Awaitable[Any]], None],
) -> tuple[bool, bool]:
    turn = agent_turn(
        content=response_text,
        agent_id=agent_id,
        agent_name=agent_name,
        timestamp=now(),
        turn_id=id_factory(),
        was_successful=was_successful,
    )
    modified, matched = await repository.push_and_trim_conversation_turn(
        room_id,
        turn,
        max_turns=MAX_HISTORY_TURNS,
        summary_stub=f"[{agent_name}] {response_text[:200]}...",
        max_summary_chars=MAX_SUMMARY_CHARS,
    )
    if modified and turn["estimated_tokens_full"] > LLM_TURN_NOTES_THRESHOLD:
        background_task_runner(
            enrich_turn_notes(
                repository=repository,
                llm_provider=llm_provider,
                llm_config=llm_config,
                room_id=room_id,
                turn_id=turn["turn_id"],
                heuristic_notes=turn.get("turn_notes"),
                content=response_text,
            )
        )
    return modified, matched


async def add_synthesis_to_history(
    *,
    repository: MemoryRepository,
    room_id: str,
    synthesis_text: str,
    trajectory: Any | None,
    id_factory: Callable[[], str],
    now,
    llm_provider: LLMProvider,
    llm_config: ContextMemoryLLMConfig,
    background_task_runner: Callable[[Awaitable[Any]], None],
) -> str | None:
    content = enrich_synthesis_with_trajectory(synthesis_text, trajectory)
    turn_id = id_factory()
    turn = supervisor_turn(turn_id=turn_id, content=content, timestamp=now())
    modified, matched = await repository.push_and_trim_conversation_turn(
        room_id,
        turn,
        max_turns=MAX_HISTORY_TURNS,
        summary_stub=f"[Supervisor synthesis ({turn_id[:8]})] {content[:200]}...",
        max_summary_chars=MAX_SUMMARY_CHARS,
    )
    if not modified or not matched:
        return None
    if turn["estimated_tokens_full"] > LLM_TURN_NOTES_THRESHOLD:
        background_task_runner(
            enrich_turn_notes(
                repository=repository,
                llm_provider=llm_provider,
                llm_config=llm_config,
                room_id=room_id,
                turn_id=turn_id,
                heuristic_notes=turn.get("turn_notes"),
                content=content,
            )
        )
    return turn_id


async def enrich_turn_notes(
    *,
    repository: MemoryRepository,
    llm_provider: LLMProvider,
    llm_config: ContextMemoryLLMConfig,
    room_id: str,
    turn_id: str,
    heuristic_notes: dict | None,
    content: str,
) -> None:
    enriched = await extract_turn_notes_llm(
        content, llm_provider=llm_provider, llm_config=llm_config
    )
    if enriched and enriched != heuristic_notes:
        await repository.update_turn_notes(room_id, turn_id, enriched)


async def extract_turn_notes_llm(
    content: str,
    *,
    llm_provider: LLMProvider,
    llm_config: ContextMemoryLLMConfig,
) -> dict | None:
    if not content or len(content.strip()) < 10:
        return None
    prompt = (
        "Extract structured notes from the following conversation turn. "
        "Return ONLY valid JSON with these keys:\n"
        '- "keywords": list of 5-10 important keywords\n'
        '- "entities": list of named entities (people, projects, tools)\n'
        '- "tags": list of topic tags (e.g. "debugging", "deployment")\n'
        '- "one_liner": a single sentence summary (max 100 chars)\n\n'
        f"Turn content:\n{content[:3000]}"
    )
    try:
        response = await llm_provider.generate_structured(
            [
                {
                    "role": "system",
                    "content": "Extract structured notes. Respond with valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            schema=TURN_NOTES_SCHEMA,
            model=llm_config.turn_notes_model,
        )
        result = getattr(response, "data", None)
        if isinstance(result, dict):
            return {
                "keywords": list(result.get("keywords", []))[:10],
                "entities": list(result.get("entities", []))[:5],
                "tags": list(result.get("tags", []))[:5],
                "one_liner": (result.get("one_liner", "") or "")[:150],
            }
    except Exception:
        pass
    return extract_turn_notes(content)


def enrich_synthesis_with_trajectory(synthesis_text: str, trajectory: Any | None) -> str:
    if not trajectory or not getattr(trajectory, "entries", None):
        return synthesis_text
    contributions: list[str] = []
    for entry in getattr(trajectory, "entries", []):
        for result in getattr(entry, "results", []):
            if getattr(result, "success", False) and getattr(result, "agent_name", None):
                contributions.append(
                    f"{result.agent_name}: {(getattr(result, 'task', '') or '')[:100]}"
                )
    if not contributions:
        return synthesis_text
    return (
        f"{synthesis_text}\n\n"
        f"[Agent contributions: {'; '.join(contributions[:5])}]"
    )


def extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        attachments = content.get("attachments")
        for key in ("message_text", "response_text", "response", "content", "message", "text"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return build_turn_content(value, attachments)
        return json.dumps(content, sort_keys=True, default=str)
    return json.dumps(content, sort_keys=True, default=str)
