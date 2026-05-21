from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from common.protocols import ContentStorageRepository, MemoryRepository
from common.utils.context_utils import estimate_tokens

from context_memory.config import CompactionConfig
from context_memory.content_storage import (
    ContentExpiredError,
    expand_mongodb_reference,
    hash_content,
    store_full_content,
)
from context_memory.translators import (
    compaction_result_dto,
    normalize_room_memory,
    turn_from_dict,
)

IndexTurnCallback = Callable[[str, dict], Awaitable[bool]]


def safe_tokens_full(turn) -> int:
    if turn.estimated_tokens_full > 0:
        return turn.estimated_tokens_full
    if turn.content:
        return estimate_tokens(turn.content)
    return 0


async def should_compact(
    repository: MemoryRepository, room_id: str, config: CompactionConfig
) -> bool:
    doc = await repository.get_room_memory(room_id)
    return _doc_should_compact(doc, config)


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
    room_id: str,
    config: CompactionConfig,
    now,
    index_turn: IndexTurnCallback | None = None,
):
    doc = await repository.get_room_memory(room_id)
    if not _doc_should_compact(doc, config):
        return None
    return await compact_room_memory(
        repository=repository,
        content_repository=content_repository,
        room_id=room_id,
        room_memory_doc=doc,
        config=config,
        now=now,
        index_turn=index_turn,
        threshold_gate=False,
    )


async def run_compaction(
    *,
    repository: MemoryRepository,
    content_repository: ContentStorageRepository,
    room_id: str,
    config: CompactionConfig,
    now,
    index_turn: IndexTurnCallback | None = None,
):
    doc = await repository.get_room_memory(room_id)
    if not _doc_should_compact(doc, config):
        return compaction_result_dto(
            room_id=room_id,
            compacted_count=0,
            tokens_saved=0,
            metadata={"skipped": True, "reason": "below_threshold"},
        )
    return await compact_room_memory(
        repository=repository,
        content_repository=content_repository,
        room_id=room_id,
        room_memory_doc=doc,
        config=config,
        now=now,
        index_turn=index_turn,
        threshold_gate=False,
    )


async def compact_room_memory(
    *,
    repository: MemoryRepository,
    content_repository: ContentStorageRepository,
    room_id: str,
    room_memory_doc: dict | None,
    config: CompactionConfig,
    now,
    index_turn: IndexTurnCallback | None = None,
    threshold_gate: bool = False,
):
    if not config.enabled:
        return compaction_result_dto(
            room_id=room_id,
            compacted_count=0,
            tokens_saved=0,
            metadata={"errors": ["Compaction is disabled"]},
        )
    doc = room_memory_doc or await repository.get_room_memory(room_id)
    if not doc:
        return compaction_result_dto(
            room_id=room_id,
            compacted_count=0,
            tokens_saved=0,
            metadata={"errors": [f"Room memory not found for room {room_id}"]},
        )
    if threshold_gate and not await should_compact(repository, room_id, config):
        return compaction_result_dto(
            room_id=room_id,
            compacted_count=0,
            tokens_saved=0,
            metadata={"skipped": True, "reason": "below_threshold"},
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
            room_id=room_id,
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
                    room_id=room_id,
                    turn_id=turn.turn_id,
                    content=turn.content,
                    content_type=turn.content_type,
                    turn_notes=turn.turn_notes,
                    now=now(),
                    config=config,
                )
                if index_turn is not None:
                    try:
                        indexed = await index_turn(room_id, turn.to_dict())
                        if indexed is False:
                            errors.append(f"Failed to index turn {turn.turn_id}")
                    except Exception as exc:
                        errors.append(f"Failed to index turn {turn.turn_id}: {exc}")
                content_ref = {
                    "storage_type": "mongodb",
                    "collection": "conversation_content",
                    "document_id": document_id,
                    "content_hash": hash_content(turn.content),
                    "created_at": now(),
                }
                entry = {
                    "turn_id": turn.turn_id,
                    "content_ref": content_ref,
                    "estimated_tokens_compact": turn.estimated_tokens_compact,
                }
                saved = max(0, safe_tokens_full(turn) - turn.estimated_tokens_compact)
                return entry, saved
            except Exception as exc:
                errors.append(f"Failed to compact turn {turn.turn_id}: {exc}")
                return None, 0

    prepared = await asyncio.gather(*(prepare(turn) for turn in candidates))
    compacted_entries = [entry for entry, _saved in prepared if entry]
    tokens_saved = sum(saved for entry, saved in prepared if entry)
    if compacted_entries:
        ok = await repository.compact_turns_bulk(room_id, compacted_entries)
        if not ok:
            stale_noop = await _compaction_write_was_stale_noop(
                repository,
                room_id,
                compacted_entries,
            )
            if not stale_noop:
                errors.append(
                    f"Prepared {len(compacted_entries)} turns but atomic write failed for room {room_id}"
                )
            compacted_entries = []
            tokens_saved = 0
    return compaction_result_dto(
        room_id=room_id,
        compacted_count=len(compacted_entries),
        tokens_saved=tokens_saved,
        memory_id=state.memory_id,
        metadata={"errors": errors, "compacted_at": now()},
    )


async def _compaction_write_was_stale_noop(
    repository: MemoryRepository,
    room_id: str,
    compacted_entries: list[dict],
) -> bool:
    fresh_doc = await repository.get_room_memory(room_id)
    if not fresh_doc:
        return False
    target_ids = {
        entry.get("turn_id")
        for entry in compacted_entries
        if entry.get("turn_id")
    }
    if not target_ids:
        return False
    state = normalize_room_memory(fresh_doc)
    return not any(
        turn.turn_id in target_ids and turn.representation == "full"
        for turn in state.conversation_history
    )


async def expand_turn_content_from_turn(
    content_repository: ContentStorageRepository,
    turn_doc: dict,
    *,
    now: datetime | None = None,
) -> str:
    turn = turn_from_dict(turn_doc)
    if turn.representation == "full":
        return turn.content or ""
    if turn.content_ref is None:
        raise ValueError(f"Compact turn {turn.turn_id} missing content reference")
    if turn.content_ref.storage_type != "mongodb":
        raise NotImplementedError(turn.content_ref.storage_type)
    return await expand_mongodb_reference(
        content_repository,
        turn.content_ref.to_dict(),
        turn.turn_id,
        now=now,
    )


async def expand_turn_content(
    repository: MemoryRepository,
    content_repository: ContentStorageRepository,
    room_id: str,
    turn_id: str,
    *,
    now: datetime | None = None,
) -> str | None:
    doc = await repository.get_room_memory(room_id)
    if not doc:
        return None
    state = normalize_room_memory(doc)
    for turn in state.conversation_history:
        if turn.turn_id == turn_id:
            return await expand_turn_content_from_turn(
                content_repository,
                turn.to_dict(),
                now=now,
            )
    return None


async def fetch_turn_content(
    repository: MemoryRepository,
    content_repository: ContentStorageRepository,
    *,
    turn_id: str,
    room_id: str,
) -> str:
    doc = await repository.get_room_memory(room_id)
    if not doc:
        return f"[Error: Room {room_id} not found]"
    state = normalize_room_memory(doc)
    turn = next((item for item in state.conversation_history if item.turn_id == turn_id), None)
    if turn is None:
        return f"[Error: Turn {turn_id} not found in room history]"
    try:
        return await expand_turn_content_from_turn(
            content_repository, turn.to_dict(), now=now
        )
    except ContentExpiredError:
        return f"[Error: Content for turn {turn_id} is no longer available (expired)]"
    except NotImplementedError as exc:
        return f"[Error: Content for turn {turn_id} uses unsupported storage: {exc}]"
    except ValueError as exc:
        return f"[Error: {exc}]"


async def get_compaction_stats(
    repository: MemoryRepository,
    content_repository: ContentStorageRepository,
    room_id: str,
) -> dict:
    doc = await repository.get_room_memory(room_id)
    if not doc:
        return {"error": f"Room {room_id} not found"}
    state = normalize_room_memory(doc)
    full_turns = [turn for turn in state.conversation_history if turn.representation == "full"]
    compact_turns = [
        turn for turn in state.conversation_history if turn.representation == "compact"
    ]
    return {
        "room_id": room_id,
        "total_turns": len(state.conversation_history),
        "full_turns": len(full_turns),
        "compact_turns": len(compact_turns),
        "full_tokens": sum(safe_tokens_full(turn) for turn in full_turns),
        "tokens_saved_by_compaction": sum(
            max(0, safe_tokens_full(turn) - turn.estimated_tokens_compact)
            for turn in compact_turns
        ),
        "total_compactions": state.total_compactions,
        "content_storage": await content_repository.get_content_stats_for_room(room_id),
    }
