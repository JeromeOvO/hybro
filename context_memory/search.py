from __future__ import annotations

import asyncio
import math
import time
from dataclasses import replace
from datetime import datetime

from common.dto import MemorySearchResult, VectorRecord
from common.errors import VectorIndexUnavailableError
from common.protocols import ContentStorageRepository, LLMProvider, VectorDAL
from common.utils.logger import get_logger
from common.utils.time import utcnow
from context_memory.config import MemorySearchConfig
from context_memory.content_storage import is_content_expired
from context_memory.models import SearchRankingRecord
from context_memory.translators import search_result_from_record

logger = get_logger(__name__)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def search_memory(
    *,
    room_id: str,
    query: str,
    limit: int,
    vector: VectorDAL,
    llm_provider: LLMProvider,
    content_repository: ContentStorageRepository,
    config: MemorySearchConfig,
) -> tuple[list[MemorySearchResult], dict]:
    start = time.monotonic()
    if not config.enabled:
        return [], {
            "query": query,
            "room_id": room_id,
            "results": [],
            "total_matches": 0,
            "search_time_ms": 0.0,
            "searched_at": utcnow(),
            "vector_search_used": False,
            "keyword_search_used": False,
            "temporal_decay_applied": False,
            "mmr_applied": False,
        }

    vector_task = vector_search(
        room_id=room_id,
        query=query,
        vector=vector,
        llm_provider=llm_provider,
        config=config,
    )
    keyword_task = keyword_search(
        room_id=room_id,
        query=query,
        content_repository=content_repository,
        config=config,
    )
    raw_vector_records, raw_keyword_records = await asyncio.gather(
        vector_task,
        keyword_task,
        return_exceptions=True,
    )

    if isinstance(raw_vector_records, Exception):
        logger.warning(
            "Vector memory search failed for room %s",
            room_id,
            exc_info=_exc_info(raw_vector_records),
        )
        vector_records = []
        vector_used = False
    else:
        vector_records = raw_vector_records
        vector_used = True

    if isinstance(raw_keyword_records, Exception):
        logger.warning(
            "Keyword memory search failed for room %s",
            room_id,
            exc_info=_exc_info(raw_keyword_records),
        )
        keyword_records = []
        keyword_used = False
    else:
        keyword_records = raw_keyword_records
        keyword_used = True

    merged = merge_results(
        vector_records,
        keyword_records,
        vector_weight=config.vector_weight,
        keyword_weight=config.keyword_weight,
    )
    decay_applied = False
    if config.temporal_decay_enabled and merged:
        merged = apply_temporal_decay(merged, config.half_life_days)
        decay_applied = True
    mmr_applied = False
    if merged:
        merged = apply_mmr(merged, config.mmr_lambda)
        mmr_applied = True

    effective_limit = _effective_limit(limit, config.max_results)
    final = merged[:effective_limit]
    await hydrate_empty_results(final, room_id, content_repository, config)
    dto_results = [
        search_result_from_record(
            room_id=record.room_id,
            content=record.content,
            score=record.combined_score,
            metadata={
                **record.metadata,
                "turn_id": record.turn_id,
                "vector_score": record.vector_score,
                "keyword_score": record.keyword_score,
                "combined_score": record.combined_score,
                "temporal_decay_factor": record.temporal_decay_factor,
            },
        )
        for record in final
    ]
    response = {
        "query": query,
        "room_id": room_id,
        "results": dto_results,
        "total_matches": len(merged),
        "search_time_ms": round((time.monotonic() - start) * 1000, 2),
        "searched_at": utcnow(),
        "vector_search_used": vector_used,
        "keyword_search_used": keyword_used,
        "temporal_decay_applied": decay_applied,
        "mmr_applied": mmr_applied,
    }
    return dto_results, response


async def vector_search(
    *,
    room_id: str,
    query: str,
    vector: VectorDAL,
    llm_provider: LLMProvider,
    config: MemorySearchConfig,
) -> list[SearchRankingRecord]:
    embedding = await llm_provider.embed(query)
    matches = await vector.search(
        config.index_name,
        embedding,
        top_k=50,
        filter={"room_id": {"$eq": room_id}},
    )
    records = []
    for match in matches:
        metadata = dict(match.metadata or {})
        timestamp = _parse_timestamp(metadata.get("timestamp"))
        records.append(
            SearchRankingRecord(
                turn_id=metadata.get("turn_id", match.id),
                room_id=room_id,
                content=metadata.get("content_preview", ""),
                vector_score=match.score,
                timestamp=timestamp,
                metadata={
                    **metadata,
                    "source_type": "turn",
                    "is_compact": True,
                    "can_expand": True,
                },
            )
        )
    return records


async def keyword_search(
    *,
    room_id: str,
    query: str,
    content_repository: ContentStorageRepository,
    config: MemorySearchConfig,
) -> list[SearchRankingRecord]:
    docs = await content_repository.text_search(room_id, query, limit=50)
    records = []
    for doc in docs:
        if is_content_expired(doc):
            continue
        notes = doc.get("turn_notes") or {}
        one_liner = notes.get("one_liner") if isinstance(notes, dict) else None
        content_type = doc.get("content_type") or "text"
        content = (
            one_liner[: config.max_snippet_chars]
            if one_liner
            else f"[{content_type}]"
        )
        content_preview = one_liner[: config.max_snippet_chars] if one_liner else None
        timestamp = _parse_timestamp(doc.get("stored_at"))
        records.append(
            SearchRankingRecord(
                turn_id=doc.get("turn_id", ""),
                room_id=room_id,
                content=content,
                keyword_score=float(doc.get("score", 0.0) or 0.0),
                timestamp=timestamp,
                metadata={
                    "source_type": "turn",
                    "content_preview": content_preview,
                    "content_type": content_type,
                    "timestamp": timestamp,
                    "is_compact": True,
                    "can_expand": True,
                },
            )
        )
    return records


def merge_results(
    vector_results: list[SearchRankingRecord],
    keyword_results: list[SearchRankingRecord],
    *,
    vector_weight: float,
    keyword_weight: float,
) -> list[SearchRankingRecord]:
    by_turn: dict[str, SearchRankingRecord] = {}
    v_max = max((r.vector_score for r in vector_results), default=1.0) or 1.0
    k_max = max((r.keyword_score for r in keyword_results), default=1.0) or 1.0
    for record in vector_results:
        if not record.turn_id:
            continue
        copy = replace(record)
        copy.vector_score = record.vector_score / v_max
        by_turn[record.turn_id] = copy
    for record in keyword_results:
        if not record.turn_id:
            continue
        normalized = record.keyword_score / k_max
        entry = by_turn.get(record.turn_id)
        if entry is None:
            copy = replace(record)
            copy.keyword_score = normalized
            by_turn[record.turn_id] = copy
        else:
            entry.keyword_score = normalized
            if record.content and not entry.content:
                entry.content = record.content
            if record.timestamp and not entry.timestamp:
                entry.timestamp = record.timestamp
            entry.metadata.update(record.metadata)
    for record in by_turn.values():
        record.combined_score = (
            vector_weight * record.vector_score + keyword_weight * record.keyword_score
        )
    return sorted(by_turn.values(), key=lambda item: item.combined_score, reverse=True)


def apply_temporal_decay(
    results: list[SearchRankingRecord], half_life_days: int
) -> list[SearchRankingRecord]:
    now = utcnow()
    if half_life_days <= 0:
        return results
    for record in results:
        if record.timestamp:
            ts = record.timestamp
            if ts.tzinfo is None:
                age_days = (now.replace(tzinfo=None) - ts).total_seconds() / 86400
            else:
                age_days = (now - ts).total_seconds() / 86400
            decay = math.pow(2, -age_days / half_life_days)
        else:
            decay = 0.5
        record.temporal_decay_factor = decay
        record.combined_score *= decay
    return sorted(results, key=lambda item: item.combined_score, reverse=True)


def apply_mmr(
    results: list[SearchRankingRecord], lambda_param: float
) -> list[SearchRankingRecord]:
    if len(results) <= 1:
        return results
    profiles = {
        i: [r.vector_score, r.keyword_score, r.temporal_decay_factor]
        for i, r in enumerate(results)
    }
    selected = [max(range(len(results)), key=lambda i: results[i].combined_score)]
    remaining = set(range(len(results))) - set(selected)
    while remaining:
        best_idx = max(
            remaining,
            key=lambda i: lambda_param * results[i].combined_score
            - (1 - lambda_param)
            * max(cosine_similarity(profiles[i], profiles[s]) for s in selected),
        )
        selected.append(best_idx)
        remaining.remove(best_idx)
    return [results[i] for i in selected]


async def hydrate_empty_results(
    results: list[SearchRankingRecord],
    room_id: str,
    content_repository: ContentStorageRepository,
    config: MemorySearchConfig,
) -> None:
    turn_ids = [record.turn_id for record in results if record.turn_id and not record.content]
    if not turn_ids:
        return
    docs = await content_repository.hydrate_turn_notes(room_id, turn_ids)
    by_turn = {
        doc.get("turn_id"): doc
        for doc in docs
        if not is_content_expired(doc)
    }
    for record in results:
        if record.content or record.turn_id not in by_turn:
            continue
        notes = by_turn[record.turn_id].get("turn_notes") or {}
        if isinstance(notes, dict) and notes.get("one_liner"):
            record.content = notes["one_liner"][: config.max_snippet_chars]
            record.metadata["content_preview"] = record.content


async def index_turn_for_search(
    *,
    room_id: str,
    turn_doc: dict,
    vector: VectorDAL,
    llm_provider: LLMProvider,
    config: MemorySearchConfig,
) -> bool:
    content = turn_doc.get("content") or ""
    if not content:
        return False
    try:
        embedding = await llm_provider.embed(content)
        await vector.upsert(
            config.index_name,
            [
                VectorRecord(
                    id=turn_doc.get("turn_id", ""),
                    vector=embedding,
                    metadata={
                        "room_id": room_id,
                        "turn_id": turn_doc.get("turn_id", ""),
                        "role": turn_doc.get("role", "unknown"),
                        "agent_name": turn_doc.get("agent_name") or "",
                        "timestamp": str(turn_doc.get("timestamp") or ""),
                    },
                )
            ],
        )
        return True
    except VectorIndexUnavailableError as exc:
        logger.warning(
            "Vector index unavailable while indexing context memory turn %s for room %s",
            turn_doc.get("turn_id", ""),
            room_id,
            exc_info=_exc_info(exc),
        )
        return False
    except Exception as exc:
        logger.warning(
            "Failed to index context memory turn %s for room %s",
            turn_doc.get("turn_id", ""),
            room_id,
            exc_info=_exc_info(exc),
        )
        return False


async def delete_room_index(
    *,
    room_id: str,
    vector: VectorDAL,
    config: MemorySearchConfig,
    unavailable_ok: bool = False,
) -> bool:
    try:
        await vector.delete_by_filter(config.index_name, {"room_id": {"$eq": room_id}})
        return True
    except VectorIndexUnavailableError:
        return unavailable_ok
    except Exception:
        return False


def _parse_timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _effective_limit(limit: int | None, default: int) -> int:
    if limit is None:
        return max(0, default)
    return max(0, limit)


def _exc_info(exc: BaseException) -> tuple[type[BaseException], BaseException, object]:
    return (type(exc), exc, exc.__traceback__)
