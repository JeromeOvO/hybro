from __future__ import annotations

import asyncio

from common.utils.context_utils import estimate_tokens
from context_memory.config import CompactionConfig
from context_memory.content_storage import hash_content, store_full_content
from context_memory.ports import ContentStorageRepository, MemoryRepository
from context_memory.translators import (
    compaction_result_dto,
    normalize_room_memory,
    turn_from_dict,
)

BRIEF_SUMMARY_MAX_CHARS = 200


def brief_summary_from_content(content: str) -> str:
    """Build a deterministic, bounded semantic preview from a full turn."""
    normalized = " ".join(content.split())
    if len(normalized) <= BRIEF_SUMMARY_MAX_CHARS:
        return normalized
    return f"{normalized[: BRIEF_SUMMARY_MAX_CHARS - 3].rstrip()}..."


def estimate_compact_turn_tokens(turn_doc: dict) -> int:
    """Estimate the actual role, summary, and pointer rendering of a compact turn."""
    compact_turn_doc = {**turn_doc, "representation": "compact", "content": None}
    return estimate_tokens(turn_from_dict(compact_turn_doc).to_context_string())


def safe_tokens_full(turn) -> int:
    if turn.estimated_tokens_full > 0:
        return turn.estimated_tokens_full
    if turn.content:
        return estimate_tokens(turn.content)
    return 0


def _doc_should_compact(doc: dict | None, config: CompactionConfig) -> bool:
    if not config.enabled or not doc:
        return False
    state = normalize_room_memory(doc)
    full_turns = [t for t in state.conversation_history if t.representation == "full"]
    if not full_turns:
        return False
    if len(full_turns) > config.max_full_turns:
        return True
    return sum(safe_tokens_full(turn) for turn in full_turns) > config.max_total_tokens


async def compact_if_needed(
    *,
    repository: MemoryRepository,
    content_repository: ContentStorageRepository,
    conversation_id: str,
    config: CompactionConfig,
    now,
):
    doc = await repository.get_room_memory(conversation_id)
    if not _doc_should_compact(doc, config):
        return None
    return await compact_room_memory(
        repository=repository,
        content_repository=content_repository,
        conversation_id=conversation_id,
        room_memory_doc=doc,
        config=config,
        now=now,
    )


async def run_compaction(
    *,
    repository: MemoryRepository,
    content_repository: ContentStorageRepository,
    conversation_id: str,
    config: CompactionConfig,
    now,
):
    doc = await repository.get_room_memory(conversation_id)
    if not _doc_should_compact(doc, config):
        return compaction_result_dto(
            conversation_id=conversation_id,
            compacted_count=0,
            tokens_saved=0,
            metadata={"skipped": True, "reason": "below_threshold"},
        )
    return await compact_room_memory(
        repository=repository,
        content_repository=content_repository,
        conversation_id=conversation_id,
        room_memory_doc=doc,
        config=config,
        now=now,
    )


async def compact_room_memory(
    *,
    repository: MemoryRepository,
    content_repository: ContentStorageRepository,
    conversation_id: str,
    room_memory_doc: dict | None,
    config: CompactionConfig,
    now,
):
    if not config.enabled:
        return compaction_result_dto(
            conversation_id=conversation_id,
            compacted_count=0,
            tokens_saved=0,
            metadata={"errors": ["Compaction is disabled"]},
        )
    doc = room_memory_doc or await repository.get_room_memory(conversation_id)
    if not doc:
        return compaction_result_dto(
            conversation_id=conversation_id,
            compacted_count=0,
            tokens_saved=0,
            metadata={"errors": [f"Room memory not found for room {conversation_id}"]},
        )

    state = normalize_room_memory(doc)
    history = state.conversation_history
    if config.preserve_recent_turns == 0:
        candidates = [turn for turn in history if turn.representation == "full"]
    else:
        candidates = [
            turn
            for turn in history[: -config.preserve_recent_turns]
            if turn.representation == "full"
        ]
    if not candidates:
        return compaction_result_dto(
            conversation_id=conversation_id,
            compacted_count=0,
            tokens_saved=0,
            memory_id=state.memory_id,
        )

    semaphore = asyncio.Semaphore(config.concurrency)
    errors: list[str] = []

    async def prepare(turn):
        async with semaphore:
            if not turn.content:
                return None, 0
            try:
                document_id = await store_full_content(
                    content_repository,
                    conversation_id=conversation_id,
                    turn_id=turn.turn_id,
                    content=turn.content,
                    content_type=turn.content_type,
                    turn_timestamp=turn.timestamp,
                    now=now(),
                    config=config,
                )
                content_ref = {
                    "storage_type": "mongodb",
                    "collection": "conversation_content",
                    "document_id": document_id,
                    "content_hash": hash_content(turn.content),
                    "created_at": now(),
                }
                brief_summary = brief_summary_from_content(turn.content)
                compact_turn_doc = {
                    **turn.to_dict(),
                    "content_ref": content_ref,
                    "brief_summary": brief_summary,
                }
                estimated_tokens_compact = estimate_compact_turn_tokens(
                    compact_turn_doc
                )
                entry = {
                    "turn_id": turn.turn_id,
                    "content_ref": content_ref,
                    "estimated_tokens_compact": estimated_tokens_compact,
                    "brief_summary": brief_summary,
                }
                saved = max(0, safe_tokens_full(turn) - estimated_tokens_compact)
                return entry, saved
            except Exception as exc:
                errors.append(f"Failed to compact turn {turn.turn_id}: {exc}")
                return None, 0

    prepared = await asyncio.gather(*(prepare(turn) for turn in candidates))
    compacted_entries = [entry for entry, _saved in prepared if entry]
    tokens_saved = sum(saved for entry, saved in prepared if entry)
    if compacted_entries:
        ok = await repository.compact_turns_bulk(conversation_id, compacted_entries)
        if not ok:
            stale_noop = await _compaction_write_was_stale_noop(
                repository,
                conversation_id,
                compacted_entries,
            )
            if not stale_noop:
                errors.append(
                    f"Prepared {len(compacted_entries)} turns but atomic write failed for room {conversation_id}"
                )
            compacted_entries = []
            tokens_saved = 0
    return compaction_result_dto(
        conversation_id=conversation_id,
        compacted_count=len(compacted_entries),
        tokens_saved=tokens_saved,
        memory_id=state.memory_id,
        metadata={"errors": errors, "compacted_at": now()},
    )


async def _compaction_write_was_stale_noop(
    repository: MemoryRepository,
    conversation_id: str,
    compacted_entries: list[dict],
) -> bool:
    fresh_doc = await repository.get_room_memory(conversation_id)
    if not fresh_doc:
        return False
    target_ids = {
        entry.get("turn_id") for entry in compacted_entries if entry.get("turn_id")
    }
    if not target_ids:
        return False
    state = normalize_room_memory(fresh_doc)
    return not any(
        turn.turn_id in target_ids and turn.representation == "full"
        for turn in state.conversation_history
    )
