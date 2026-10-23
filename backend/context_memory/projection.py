from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from common.protocols import RoomHistoryReader
from common.utils.context_utils import (
    MAX_HISTORY_TURNS,
    MAX_SUMMARY_CHARS,
    clean_mention_format,
    estimate_tokens,
)
from context_memory.ports import MemoryRepository


def build_turn_content(message_text: str, attachments: list[Any] | None = None) -> str:
    content = message_text or ""
    if not attachments:
        return content
    descriptions = [
        _format_attachment_for_turn(attachment) for attachment in attachments
    ]
    return f"{content}\n[Attachments: {', '.join(descriptions)}]"


def _format_attachment_for_turn(attachment: Any) -> str:
    name = _attachment_value(attachment, "file_name", "name") or "attachment"
    mime_type = (
        _attachment_value(attachment, "mime_type", "content_type") or "unknown type"
    )
    size_bytes = _attachment_value(attachment, "size_bytes", "size")
    size = _human_size(size_bytes) if size_bytes else "unknown size"
    return f"{name} ({mime_type}, {size})"


def _attachment_value(attachment: Any, *keys: str) -> Any:
    if isinstance(attachment, dict):
        for key in keys:
            if attachment.get(key) is not None:
                return attachment[key]
        return None
    for key in keys:
        value = getattr(attachment, key, None)
        if value is not None:
            return value
    return None


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f}KB"
    return f"{size_bytes / (1024 * 1024):.1f}MB"


async def project_message_from_history(
    *,
    conversation_id: str,
    message_id: str,
    repository: MemoryRepository,
    room_history_reader: RoomHistoryReader,
    id_factory: Callable[[], str],
    now,
    room_agent_set: dict[str, str] | None = None,
    agent_name: str | None = None,
    was_successful: bool | None = None,
) -> dict:
    messages = await room_history_reader.get_messages_by_ids([message_id])
    message = next(
        (m for m in messages if getattr(m, "message_id", None) == message_id), None
    )
    if message is None:
        return {"projected": False, "reason": "missing_message"}
    if message.conversation_id != conversation_id:
        return {"projected": False, "reason": "room_mismatch"}

    message_type = getattr(message, "message_type", "user")
    message_content = getattr(message, "content", None)
    raw_message_text = extract_raw_message_text(message_content)
    content_text = extract_message_text(message_content)
    if not content_text:
        return {"projected": False, "reason": "empty_content"}

    if message_type == "user":
        clean_message = clean_mention_format(raw_message_text, room_agent_set or {})
        content_text = build_turn_content(
            clean_message,
            extract_message_attachments(message_content),
        )
        defaults = new_room_memory_doc(
            conversation_id=conversation_id,
            memory_id=id_factory(),
            now=now(),
        )
        await repository.ensure_room_memory(conversation_id, defaults)
        turn = user_turn(
            message_id=message_id,
            content=content_text,
            user_id=getattr(message, "sender_id", None),
            timestamp=getattr(message, "created_at", None) or now(),
        )
    else:
        if not await repository.get_room_memory(conversation_id):
            return {"projected": False, "reason": "missing_room_memory"}
        turn = agent_turn_from_projected_message(
            message=message,
            message_id=message_id,
            content=content_text,
            now=now,
            agent_name=agent_name,
            was_successful=was_successful,
        )

    (
        modified,
        matched,
        already_exists,
    ) = await repository.push_and_trim_conversation_turn_if_absent(
        conversation_id,
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


def agent_turn_from_projected_message(
    *,
    message: Any,
    message_id: str,
    content: str,
    now,
    agent_name: str | None,
    was_successful: bool | None,
) -> dict:
    resolved_agent_name = (
        agent_name
        or getattr(message, "sender_name", None)
        or _message_metadata_value(message, "agent_name")
    )
    resolved_was_successful = was_successful
    if resolved_was_successful is None:
        resolved_was_successful = getattr(message, "was_successful", None)
    if resolved_was_successful is None:
        resolved_was_successful = _message_metadata_value(
            message, "was_successful", "success"
        )
    return agent_turn(
        content=content,
        agent_id=getattr(message, "agent_id", None)
        or getattr(message, "sender_id", None),
        agent_name=resolved_agent_name,
        timestamp=getattr(message, "created_at", None) or now(),
        turn_id=f"message:{message_id}",
        was_successful=resolved_was_successful,
    )


def new_room_memory_doc(*, conversation_id: str, memory_id: str, now) -> dict:
    return {
        "room_id": conversation_id,
        "memory_id": memory_id,
        "memory_content": {"summary": None},
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
        "was_successful": was_successful,
    }


def extract_message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if not content:
            return ""
        attachments = content.get("attachments")
        for key in (
            "message_text",
            "response_text",
            "response",
            "content",
            "message",
            "text",
        ):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return build_turn_content(value, attachments)
        return json.dumps(content, sort_keys=True, default=str)
    return json.dumps(content, sort_keys=True, default=str)


def extract_raw_message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in (
            "message_text",
            "response_text",
            "response",
            "content",
            "message",
            "text",
        ):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""
    return json.dumps(content, sort_keys=True, default=str)


def extract_message_attachments(content: Any) -> list[Any] | None:
    if isinstance(content, dict):
        attachments = content.get("attachments")
        if isinstance(attachments, list):
            return attachments
    return None


def _message_metadata_value(message: Any, *keys: str) -> Any:
    metadata = getattr(message, "metadata", None)
    if isinstance(metadata, dict):
        for key in keys:
            if metadata.get(key) is not None:
                return metadata[key]
    return None
