from __future__ import annotations

import math
import time
from datetime import datetime

from common.dto import MemorySearchResult
from common.protocols import ContentStorageRepository
from common.utils.logger import get_logger
from common.utils.time import utcnow
from context_memory.config import MemorySearchConfig
from context_memory.content_storage import is_content_expired
from context_memory.models import SearchRankingRecord
from context_memory.translators import search_result_from_record

logger = get_logger(__name__)


async def search_memory(  # noqa: C901
    *,
    room_id: str,
    query: str,
    limit: int,
    content_repository: ContentStorageRepository,
    config: MemorySearchConfig,
) -> tuple[list[MemorySearchResult], dict]:
    start = time.monotonic()
    empty_response = {
        "query": query,
        "room_id": room_id,
        "results": [],
        "total_matches": 0,
        "search_time_ms": 0.0,
        "searched_at": utcnow(),
        "keyword_search_used": False,
        "temporal_decay_applied": False,
    }
    effective_limit = _effective_limit(limit, config.max_results)
    if not config.enabled or not query.strip() or effective_limit == 0:
        return [], empty_response

    hydration_batch_size = max(50, effective_limit * 3)
    attempted_hydration_ids: set[str] = set()
    hydrated: dict[str, dict] = {}
    keyword_used = False

    try:
        docs = await content_repository.scan_text_search(room_id, query)
        keyword_used = True
    except Exception:
        logger.warning(
            "Keyword memory search failed for room %s",
            room_id,
            exc_info=True,
        )
        docs = []

    raw_records = _records_from_keyword_docs(room_id, docs)
    ranked = rank_keyword_results(
        raw_records,
        temporal_decay_enabled=config.temporal_decay_enabled,
        half_life_days=config.half_life_days,
    )
    # Hydrate every ranked candidate at most once, in bounded ``$in`` batches.
    # We cannot stop merely after finding ``limit`` snippets: a lower raw text
    # score later in the cursor may outrank an old record after temporal decay.
    for start_index in range(0, len(ranked), hydration_batch_size):
        pending_ids = [
            record.turn_id
            for record in ranked[start_index : start_index + hydration_batch_size]
            if record.turn_id and record.turn_id not in attempted_hydration_ids
        ]
        if not pending_ids:
            continue
        attempted_hydration_ids.update(pending_ids)
        try:
            hydrated_docs = await content_repository.hydrate_turn_content(
                room_id,
                pending_ids,
            )
        except Exception:
            logger.warning(
                "Memory content hydration failed for room %s",
                room_id,
                exc_info=True,
            )
            hydrated_docs = []
        for doc in hydrated_docs:
            turn_id = str(doc.get("turn_id") or "")
            if turn_id and not is_content_expired(doc):
                hydrated[turn_id] = doc
    final: list[SearchRankingRecord] = []
    for record in ranked:
        doc = hydrated.get(record.turn_id)
        if doc is None:
            continue
        record.content = _snippet_from_document(doc)[: config.max_snippet_chars]
        if not record.content:
            continue
        record.metadata.update(
            {
                "content_preview": record.content,
                "content_type": doc.get("content_type") or "text",
            }
        )
        final.append(record)
        if len(final) >= effective_limit:
            break

    dto_results = [
        search_result_from_record(
            room_id=record.room_id,
            content=record.content,
            keyword_score=record.keyword_score,
            relevance_score=record.relevance_score,
            temporal_decay_factor=record.temporal_decay_factor,
            metadata={
                **record.metadata,
                "turn_id": record.turn_id,
            },
        )
        for record in final
    ]
    response = {
        "query": query,
        "room_id": room_id,
        "results": dto_results,
        "total_matches": len(raw_records),
        "search_time_ms": round((time.monotonic() - start) * 1000, 2),
        "searched_at": utcnow(),
        "keyword_search_used": keyword_used,
        "temporal_decay_applied": bool(
            config.temporal_decay_enabled and raw_records
        ),
    }
    return dto_results, response


def rank_keyword_results(
    results: list[SearchRankingRecord],
    *,
    temporal_decay_enabled: bool,
    half_life_days: int,
) -> list[SearchRankingRecord]:
    for record in results:
        if record.raw_keyword_score is None:
            record.raw_keyword_score = record.keyword_score
    maximum = max(
        (record.raw_keyword_score or 0.0 for record in results),
        default=0.0,
    )
    now = utcnow()
    for record in results:
        normalized = (
            (record.raw_keyword_score or 0.0) / maximum if maximum > 0 else 0.0
        )
        decay = (
            _temporal_decay(record.timestamp, now, half_life_days)
            if temporal_decay_enabled
            else 1.0
        )
        record.keyword_score = normalized
        record.temporal_decay_factor = decay
        record.relevance_score = normalized * decay
    return sorted(
        results,
        key=lambda item: (
            -item.relevance_score,
            -_timestamp_sort_value(item.timestamp),
            item.turn_id,
        ),
    )


def _records_from_keyword_docs(
    room_id: str,
    docs: list[dict],
) -> list[SearchRankingRecord]:
    records: list[SearchRankingRecord] = []
    for doc in docs:
        if is_content_expired(doc):
            continue
        timestamp = _parse_timestamp(doc.get("turn_timestamp")) or _parse_timestamp(
            doc.get("stored_at")
        )
        records.append(
            SearchRankingRecord(
                turn_id=str(doc.get("turn_id") or ""),
                room_id=room_id,
                keyword_score=float(doc.get("score", 0.0) or 0.0),
                raw_keyword_score=float(doc.get("score", 0.0) or 0.0),
                timestamp=timestamp,
                metadata={
                    "source_type": "turn",
                    "timestamp": timestamp,
                    "is_compact": True,
                    "can_expand": True,
                },
            )
        )
    return [record for record in records if record.turn_id]


def _temporal_decay(
    timestamp: datetime | None,
    now: datetime,
    half_life_days: int,
) -> float:
    if timestamp is None or half_life_days <= 0:
        return 1.0
    if timestamp.tzinfo is None:
        age_seconds = (now.replace(tzinfo=None) - timestamp).total_seconds()
    else:
        age_seconds = (now - timestamp).total_seconds()
    age_days = max(0.0, age_seconds / 86400)
    return math.pow(2, -age_days / half_life_days)


def _timestamp_sort_value(timestamp: datetime | None) -> float:
    if timestamp is None:
        return float("-inf")
    try:
        return timestamp.timestamp()
    except (OSError, ValueError):
        return float("-inf")


def _parse_timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _snippet_from_document(document: dict | None) -> str:
    if document is None:
        return ""
    notes = document.get("turn_notes") or {}
    one_liner = notes.get("one_liner") if isinstance(notes, dict) else None
    return str(one_liner or document.get("content") or "")


def _effective_limit(limit: int | None, default: int) -> int:
    if limit is None:
        return max(0, default)
    return max(0, min(limit, default))
