from __future__ import annotations

from typing import Any

from common.dto import CompactionResult
from context_memory.models import (
    ContentReferenceData,
    ConversationTurnData,
    RoomMemoryState,
    RoomSummaryData,
)


def primitive(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: primitive(val) for key, val in value.items()}
    if isinstance(value, list):
        return [primitive(item) for item in value]
    if isinstance(value, tuple):
        return [primitive(item) for item in value]
    if hasattr(value, "model_dump"):
        return primitive(value.model_dump(mode="json"))
    if hasattr(value, "value") and not isinstance(value, str):
        return value.value
    return value


def normalize_room_memory(memory: dict | Any) -> RoomMemoryState:
    doc = primitive(memory) or {}
    memory_content = doc.get("memory_content") or {}
    direct_history = doc.get("conversation_history")
    history = direct_history if isinstance(direct_history, list) else []

    return RoomMemoryState(
        conversation_id=doc.get("room_id", ""),
        memory_id=doc.get("memory_id", ""),
        conversation_history=[turn_from_dict(turn) for turn in history],
        summary=memory_content.get("summary"),
        room_summary=summary_from_dict(doc.get("room_summary") or {}),
        room_facts=list(doc.get("room_facts") or []),
        memory_created_at=doc.get("memory_created_at") or doc.get("created_at"),
        last_activity_at=doc.get("last_activity_at") or doc.get("updated_at"),
        total_messages=int(doc.get("total_messages") or 0),
        total_compactions=int(doc.get("total_compactions") or 0),
        raw=doc,
    )


def summary_from_dict(doc: dict[str, Any]) -> RoomSummaryData:
    doc = primitive(doc) or {}
    return RoomSummaryData(
        current_goal=doc.get("current_goal"),
        key_decisions=list(doc.get("key_decisions") or []),
        open_questions=list(doc.get("open_questions") or []),
        recent_agent_contributions=list(doc.get("recent_agent_contributions") or []),
        important_constraints=list(doc.get("important_constraints") or []),
        last_updated_at=doc.get("last_updated_at"),
        updated_after_turn_id=doc.get("updated_after_turn_id"),
    )


def content_ref_from_dict(doc: dict[str, Any] | None) -> ContentReferenceData | None:
    if not doc:
        return None
    doc = primitive(doc)
    return ContentReferenceData(
        storage_type=doc.get("storage_type", "mongodb"),
        collection=doc.get("collection", "conversation_content"),
        document_id=doc.get("document_id"),
        content_hash=doc.get("content_hash"),
        created_at=doc.get("created_at"),
        url=doc.get("url"),
    )


def turn_from_dict(doc: dict[str, Any] | Any) -> ConversationTurnData:
    doc = primitive(doc) or {}
    role = doc.get("role", "user")
    if not isinstance(role, str):
        role = getattr(role, "value", "user")
    representation = doc.get("representation", "full")
    if not isinstance(representation, str):
        representation = getattr(representation, "value", "full")
    content_type = doc.get("content_type", "text")
    if not isinstance(content_type, str):
        content_type = getattr(content_type, "value", "text")
    turn_type = doc.get("turn_type", "message")
    if not isinstance(turn_type, str):
        turn_type = getattr(turn_type, "value", "message")
    return ConversationTurnData(
        turn_id=doc.get("turn_id") or "",
        role=role,
        content=doc.get("content"),
        agent_id=doc.get("agent_id"),
        agent_name=doc.get("agent_name"),
        user_id=doc.get("user_id"),
        timestamp=doc.get("timestamp"),
        representation=representation,
        content_ref=content_ref_from_dict(doc.get("content_ref")),
        content_type=content_type,
        turn_type=turn_type,
        estimated_tokens_full=int(doc.get("estimated_tokens_full") or 0),
        estimated_tokens_compact=int(doc.get("estimated_tokens_compact") or 20),
        brief_summary=doc.get("brief_summary"),
        was_successful=doc.get("was_successful"),
    )


def compaction_result_dto(
    *,
    conversation_id: str,
    compacted_count: int,
    tokens_saved: int,
    memory_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CompactionResult:
    return CompactionResult(
        conversation_id=conversation_id,
        compacted_count=compacted_count,
        tokens_saved=tokens_saved,
        memory_id=memory_id,
        metadata=metadata or {},
    )
