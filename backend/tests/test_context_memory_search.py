from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from common.dto import VectorSearchResult
from common.errors import VectorIndexUnavailableError
from context_memory import search
from context_memory.config import MemorySearchConfig
from context_memory.models import SearchRankingRecord

NOW = datetime.now(UTC)


class FakeLLM:
    async def embed(self, text: str):
        return [1.0, 0.0]


class FakeVector:
    def __init__(self, matches=None):
        self.matches = matches or []
        self.upserted = []
        self.deleted = []

    async def search(self, index, vector, top_k, filter=None):
        return self.matches

    async def upsert(self, index, records):
        self.upserted.append((index, records))

    async def delete_by_filter(self, index, filter):
        self.deleted.append((index, filter))


class UnavailableVector(FakeVector):
    async def search(self, index, vector, top_k, filter=None):
        raise VectorIndexUnavailableError(index, "search")


class RaisingVector(FakeVector):
    async def search(self, index, vector, top_k, filter=None):
        raise RuntimeError("vector search failed")

    async def upsert(self, index, records):
        raise RuntimeError("vector upsert failed")


class DeleteUnavailableVector(FakeVector):
    async def delete_by_filter(self, index, filter):
        raise VectorIndexUnavailableError(index, "delete")


class FakeContentRepository:
    def __init__(self, *, text_results=None, hydrate_results=None):
        self.text_results = text_results or []
        self.hydrate_results = hydrate_results or []

    async def text_search(self, room_id: str, query: str, limit: int = 50):
        return self.text_results

    async def hydrate_turn_notes(self, room_id: str, turn_ids: list[str]):
        return [
            doc for doc in self.hydrate_results if doc.get("turn_id") in set(turn_ids)
        ]


class SlowVector(FakeVector):
    async def search(self, index, vector, top_k, filter=None):
        await asyncio.sleep(0.15)
        return [
            SimpleNamespace(
                id="t1",
                score=0.8,
                metadata={"turn_id": "t1", "content_preview": "vector preview"},
            )
        ]


class SlowContentRepository(FakeContentRepository):
    async def text_search(self, room_id: str, query: str, limit: int = 50):
        await asyncio.sleep(0.15)
        return [
            {
                "turn_id": "t2",
                "score": 1.0,
                "turn_notes": {"one_liner": "keyword preview"},
                "content_type": "text",
                "stored_at": NOW,
            }
        ]


def config(**overrides):
    values = {
        "enabled": True,
        "vector_weight": 0.7,
        "keyword_weight": 0.3,
        "temporal_decay_enabled": False,
        "half_life_days": 30,
        "mmr_lambda": 0.8,
        "max_results": 10,
        "max_snippet_chars": 50,
        "index_name": "memory",
    }
    values.update(overrides)
    return MemorySearchConfig(**values)


def record(turn_id: str, *, vector=0.0, keyword=0.0, combined=0.0, timestamp=None):
    return SearchRankingRecord(
        turn_id=turn_id,
        room_id="r1",
        content=f"content {turn_id}",
        vector_score=vector,
        keyword_score=keyword,
        combined_score=combined,
        timestamp=timestamp,
    )


@pytest.mark.asyncio
async def test_search_memory_disabled():
    results, response = await search.search_memory(
        room_id="r1",
        query="hello",
        limit=10,
        vector=FakeVector(),
        llm_provider=FakeLLM(),
        content_repository=FakeContentRepository(),
        config=config(enabled=False),
    )

    assert results == []
    assert response["vector_search_used"] is False
    assert response["keyword_search_used"] is False


@pytest.mark.asyncio
async def test_vector_search_basic():
    results = await search.vector_search(
        room_id="r1",
        query="hello",
        vector=FakeVector(
            [
                VectorSearchResult(
                    id="v1",
                    score=0.9,
                    metadata={"turn_id": "t1", "content_preview": "preview"},
                )
            ]
        ),
        llm_provider=FakeLLM(),
        config=config(),
    )

    assert results[0].turn_id == "t1"
    assert results[0].content == "preview"
    assert results[0].metadata["can_expand"] is True


@pytest.mark.asyncio
async def test_search_memory_logs_vector_exception(caplog):
    caplog.set_level("WARNING")

    await search.search_memory(
        room_id="r1",
        query="hello",
        limit=10,
        vector=RaisingVector(),
        llm_provider=FakeLLM(),
        content_repository=FakeContentRepository(),
        config=config(),
    )

    assert "Vector memory search failed for room r1" in caplog.text
    record = next(
        item
        for item in caplog.records
        if item.message == "Vector memory search failed for room r1"
    )
    assert record.exc_info[0] is RuntimeError
    assert str(record.exc_info[1]) == "vector search failed"


@pytest.mark.asyncio
async def test_keyword_search_basic():
    results = await search.keyword_search(
        room_id="r1",
        query="hello",
        content_repository=FakeContentRepository(
            text_results=[
                {
                    "turn_id": "t1",
                    "score": 4.0,
                    "turn_notes": {"one_liner": "matched line"},
                    "content": "full matched line",
                    "content_type": "text",
                    "stored_at": NOW,
                }
            ]
        ),
        config=config(),
    )

    assert results[0].turn_id == "t1"
    assert results[0].content == "matched line"
    assert results[0].keyword_score == 4.0
    assert results[0].metadata["timestamp"] == NOW


@pytest.mark.asyncio
async def test_keyword_search_does_not_render_expired_content_rows():
    results = await search.keyword_search(
        room_id="r1",
        query="hello",
        content_repository=FakeContentRepository(
            text_results=[
                {
                    "turn_id": "expired",
                    "score": 4.0,
                    "turn_notes": {"one_liner": "expired snippet"},
                    "content_type": "text",
                    "stored_at": NOW,
                    "expires_at": NOW - timedelta(days=1),
                },
                {
                    "turn_id": "active",
                    "score": 3.0,
                    "turn_notes": {"one_liner": "active snippet"},
                    "content_type": "text",
                    "stored_at": NOW,
                    "expires_at": NOW + timedelta(days=1),
                },
            ]
        ),
        config=config(),
    )

    assert [result.turn_id for result in results] == ["active"]
    assert results[0].content == "active snippet"


@pytest.mark.asyncio
async def test_keyword_search_uses_type_placeholder_without_one_liner():
    results = await search.keyword_search(
        room_id="r1",
        query="hello",
        content_repository=FakeContentRepository(
            text_results=[
                {
                    "turn_id": "t1",
                    "score": 4.0,
                    "turn_notes": {},
                    "content": "large body should not be returned",
                    "content_type": "tool_result",
                    "stored_at": NOW,
                }
            ]
        ),
        config=config(),
    )

    assert results[0].content == "[tool_result]"
    assert results[0].metadata["content_preview"] is None


def test_merge_results_deduplicates_by_turn_id():
    merged = search.merge_results(
        [record("t1", vector=0.5), record("t2", vector=1.0)],
        [record("t1", keyword=4.0)],
        vector_weight=0.7,
        keyword_weight=0.3,
    )

    assert [item.turn_id for item in merged].count("t1") == 1
    t1 = next(item for item in merged if item.turn_id == "t1")
    assert t1.vector_score == 0.5
    assert t1.keyword_score == 1.0


def test_temporal_decay_reduces_old_scores():
    recent = record("recent", combined=1.0, timestamp=NOW)
    old = record("old", combined=1.0, timestamp=NOW - timedelta(days=60))

    decayed = search.apply_temporal_decay([old, recent], half_life_days=30)

    assert decayed[0].turn_id == "recent"
    assert old.combined_score < recent.combined_score


def test_mmr_reorders_for_diversity():
    results = [
        record("t1", vector=1.0, keyword=0.0, combined=0.9),
        record("t2", vector=1.0, keyword=0.0, combined=0.85),
        record("t3", vector=0.0, keyword=1.0, combined=0.5),
    ]

    reranked = search.apply_mmr(results, lambda_param=0.1)

    assert [item.turn_id for item in reranked[:2]] == ["t1", "t3"]


@pytest.mark.asyncio
async def test_hydrate_empty_results():
    results = [record("t1")]
    results[0].content = ""

    await search.hydrate_empty_results(
        results,
        "r1",
        FakeContentRepository(
            hydrate_results=[{"turn_id": "t1", "turn_notes": {"one_liner": "hydrated"}}]
        ),
        config=config(max_snippet_chars=20),
    )

    assert results[0].content == "hydrated"
    assert results[0].metadata["content_preview"] == "hydrated"


@pytest.mark.asyncio
async def test_hydrate_empty_results_does_not_render_expired_content_rows():
    results = [record("expired"), record("active")]
    for result in results:
        result.content = ""

    await search.hydrate_empty_results(
        results,
        "r1",
        FakeContentRepository(
            hydrate_results=[
                {
                    "turn_id": "expired",
                    "turn_notes": {"one_liner": "expired hydrated"},
                    "expires_at": NOW - timedelta(days=1),
                },
                {
                    "turn_id": "active",
                    "turn_notes": {"one_liner": "active hydrated"},
                    "expires_at": NOW + timedelta(days=1),
                },
            ]
        ),
        config=config(max_snippet_chars=20),
    )

    assert results[0].content == ""
    assert results[1].content == "active hydrated"


@pytest.mark.asyncio
async def test_index_turn_for_search():
    vector = FakeVector()

    ok = await search.index_turn_for_search(
        room_id="r1",
        turn_doc={"turn_id": "t1", "role": "user", "content": "index me"},
        vector=vector,
        llm_provider=FakeLLM(),
        config=config(),
    )

    assert ok is True
    assert vector.upserted[0][1][0].id == "t1"


@pytest.mark.asyncio
async def test_index_turn_for_search_logs_exception(caplog):
    caplog.set_level("WARNING")

    ok = await search.index_turn_for_search(
        room_id="r1",
        turn_doc={"turn_id": "t1", "role": "user", "content": "index me"},
        vector=RaisingVector(),
        llm_provider=FakeLLM(),
        config=config(),
    )

    assert ok is False
    assert "Failed to index context memory turn t1 for room r1" in caplog.text
    record = next(
        item
        for item in caplog.records
        if item.message == "Failed to index context memory turn t1 for room r1"
    )
    assert record.exc_info[0] is RuntimeError
    assert str(record.exc_info[1]) == "vector upsert failed"


@pytest.mark.asyncio
async def test_delete_room_index():
    vector = FakeVector()

    assert await search.delete_room_index(room_id="r1", vector=vector, config=config())
    assert vector.deleted == [("memory", {"room_id": {"$eq": "r1"}})]


@pytest.mark.asyncio
async def test_delete_room_index_unavailable_returns_false():
    assert (
        await search.delete_room_index(
            room_id="r1",
            vector=DeleteUnavailableVector(),
            config=config(),
        )
        is False
    )


@pytest.mark.asyncio
async def test_search_memory_full_pipeline():
    results, response = await search.search_memory(
        room_id="r1",
        query="hello",
        limit=5,
        vector=FakeVector(
            [
                SimpleNamespace(
                    id="t1",
                    score=0.8,
                    metadata={"turn_id": "t1", "content_preview": "vector preview"},
                )
            ]
        ),
        llm_provider=FakeLLM(),
        content_repository=FakeContentRepository(
            text_results=[
                {
                    "turn_id": "t2",
                    "score": 2.0,
                    "turn_notes": {"one_liner": "keyword preview"},
                    "content_type": "text",
                    "stored_at": NOW,
                }
            ]
        ),
        config=config(mmr_lambda=0.9),
    )

    assert {result.metadata["turn_id"] for result in results} == {"t1", "t2"}
    assert response["total_matches"] == 2
    assert response["vector_search_used"] is True
    assert response["keyword_search_used"] is True


@pytest.mark.asyncio
async def test_search_memory_limit_zero_returns_no_results():
    results, response = await search.search_memory(
        room_id="r1",
        query="hello",
        limit=0,
        vector=FakeVector(
            [
                SimpleNamespace(
                    id="t1",
                    score=0.8,
                    metadata={"turn_id": "t1", "content_preview": "vector preview"},
                )
            ]
        ),
        llm_provider=FakeLLM(),
        content_repository=FakeContentRepository(
            text_results=[
                {
                    "turn_id": "t2",
                    "score": 2.0,
                    "turn_notes": {"one_liner": "keyword preview"},
                    "content_type": "text",
                    "stored_at": NOW,
                }
            ]
        ),
        config=config(),
    )

    assert results == []
    assert response["results"] == []
    assert response["total_matches"] == 2


@pytest.mark.asyncio
async def test_search_memory_negative_limit_returns_no_results():
    results, response = await search.search_memory(
        room_id="r1",
        query="hello",
        limit=-1,
        vector=FakeVector(
            [
                SimpleNamespace(
                    id="t1",
                    score=0.8,
                    metadata={"turn_id": "t1", "content_preview": "vector preview"},
                )
            ]
        ),
        llm_provider=FakeLLM(),
        content_repository=FakeContentRepository(),
        config=config(),
    )

    assert results == []
    assert response["results"] == []
    assert response["total_matches"] == 1


@pytest.mark.asyncio
async def test_search_memory_runs_vector_and_keyword_in_parallel():
    start = time.monotonic()

    results, response = await search.search_memory(
        room_id="r1",
        query="hello",
        limit=5,
        vector=SlowVector(),
        llm_provider=FakeLLM(),
        content_repository=SlowContentRepository(),
        config=config(),
    )

    elapsed = time.monotonic() - start
    assert {result.metadata["turn_id"] for result in results} == {"t1", "t2"}
    assert response["vector_search_used"] is True
    assert response["keyword_search_used"] is True
    assert elapsed < 0.25


@pytest.mark.asyncio
async def test_search_memory_reports_vector_unused_when_index_unavailable():
    results, response = await search.search_memory(
        room_id="r1",
        query="hello",
        limit=5,
        vector=UnavailableVector(),
        llm_provider=FakeLLM(),
        content_repository=FakeContentRepository(
            text_results=[
                {
                    "turn_id": "t1",
                    "score": 1.0,
                    "turn_notes": {"one_liner": "keyword fallback"},
                    "content_type": "text",
                    "stored_at": NOW,
                }
            ]
        ),
        config=config(),
    )

    assert len(results) == 1
    assert response["vector_search_used"] is False
    assert response["keyword_search_used"] is True
