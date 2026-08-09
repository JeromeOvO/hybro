from __future__ import annotations

from typing import Any

from common.dto import (
    AssembledContext,
    CompactionResult,
    ContextBlock,
    MemorySearchResult,
)
from context_memory.models import (
    AssemblyResult,
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
        room_id=doc.get("room_id", ""),
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
        turn_notes=doc.get("turn_notes"),
        was_successful=doc.get("was_successful"),
    )


def assemble_context_dto(
    *,
    room_id: str,
    stable_prefix: str,
    dynamic_suffix: str,
    result: AssemblyResult,
    mode: str,
    extra_metadata: dict[str, Any] | None = None,
) -> AssembledContext:
    metadata = {
        "context": result.context,
        "occupancy_pct": result.occupancy_pct,
        "was_truncated": result.was_truncated,
        "truncation_reason": (
            result.truncation_reason.value if result.truncation_reason else None
        ),
        "turns_included": result.turns_included,
        "turns_truncated": result.turns_truncated,
        "stable_prefix_tokens": result.stable_prefix_tokens,
        "dynamic_suffix_tokens": result.dynamic_suffix_tokens,
        "mode": mode,
    }
    metadata.update(extra_metadata or {})
    return AssembledContext(
        room_id=room_id,
        blocks=[
            ContextBlock(
                block_id="stable_prefix",
                room_id=room_id,
                content=stable_prefix,
                token_count=result.stable_prefix_tokens,
                block_type="stable_prefix",
            ),
            ContextBlock(
                block_id="dynamic_suffix",
                room_id=room_id,
                content=dynamic_suffix,
                token_count=result.dynamic_suffix_tokens,
                block_type="dynamic_suffix",
            ),
        ],
        total_tokens=result.total_tokens,
        metadata=metadata,
    )


def search_result_from_record(
    *,
    room_id: str,
    content: str,
    keyword_score: float,
    relevance_score: float,
    temporal_decay_factor: float,
    metadata: dict[str, Any],
) -> MemorySearchResult:
    return MemorySearchResult(
        room_id=room_id,
        content=content,
        keyword_score=keyword_score,
        relevance_score=relevance_score,
        temporal_decay_factor=temporal_decay_factor,
        memory_id=metadata.get("memory_id"),
        source_message_id=metadata.get("source_message_id") or metadata.get("turn_id"),
        metadata=metadata,
    )


def compaction_result_dto(
    *,
    room_id: str,
    compacted_count: int,
    tokens_saved: int,
    memory_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CompactionResult:
    return CompactionResult(
        room_id=room_id,
        compacted_count=compacted_count,
        tokens_saved=tokens_saved,
        memory_id=memory_id,
        metadata=metadata or {},
    )
