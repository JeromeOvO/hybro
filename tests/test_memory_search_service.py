"""
Unit tests for context memory search runtime behavior.

Tests cover:
- Vector search
- Keyword search
- Result merging with weighted scoring
- Temporal decay with configurable half-life
- MMR re-ranking with hand-crafted score profiles
- Turn indexing write path
- Graceful degradation on failures
- End-to-end search pipeline

See CONTEXT_MEMORY_SYSTEM_DESIGN.md section 8 for design specification.
"""

import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from common.dto import VectorSearchResult
from context_memory import search
from context_memory.config import MemorySearchConfig
from context_memory.models import SearchRankingRecord

NOW = datetime.now(UTC)


class FakeLLM:
    def __init__(self, embedding: list[float] | None = None):
        self.embedding = embedding or [0.1] * 1536
        self.embedded = []

    async def embed(self, text: str):
        self.embedded.append(text)
        return self.embedding


class FakeVector:
    def __init__(self, matches=None):
        self.matches = matches or []
        self.search_calls = []
        self.upserted = []
        self.deleted = []

    async def search(self, index, vector, top_k, filter=None):
        self.search_calls.append((index, vector, top_k, filter))
        return self.matches

    async def upsert(self, index, records):
        self.upserted.append((index, records))

    async def delete_by_filter(self, index, filter):
        self.deleted.append((index, filter))


class RaisingVector(FakeVector):
    async def search(self, index, vector, top_k, filter=None):
        raise RuntimeError("vector unavailable")

    async def upsert(self, index, records):
        raise RuntimeError("upsert unavailable")


class RaisingContentRepository:
    async def text_search(self, room_id: str, query: str, limit: int = 50):
        raise RuntimeError("keyword unavailable")

    async def hydrate_turn_notes(self, room_id: str, turn_ids: list[str]):
        return []


class FakeContentRepository:
    def __init__(self, *, text_results=None, hydrate_results=None):
        self.text_results = text_results or []
        self.hydrate_results = hydrate_results or []
        self.text_calls = []
        self.hydrate_calls = []

    async def text_search(self, room_id: str, query: str, limit: int = 50):
        self.text_calls.append((room_id, query, limit))
        return self.text_results

    async def hydrate_turn_notes(self, room_id: str, turn_ids: list[str]):
        self.hydrate_calls.append((room_id, turn_ids))
        wanted = set(turn_ids)
        return [doc for doc in self.hydrate_results if doc.get("turn_id") in wanted]


def config(**overrides):
    values = {
        "enabled": True,
        "vector_weight": 0.7,
        "keyword_weight": 0.3,
        "temporal_decay_enabled": True,
        "half_life_days": 30,
        "mmr_lambda": 0.7,
        "max_results": 10,
        "max_snippet_chars": 500,
        "index_name": "room-memory",
    }
    values.update(overrides)
    return MemorySearchConfig(**values)


def record(
    turn_id: str,
    *,
    room_id: str = "room-1",
    content: str = "",
    vector_score: float = 0.0,
    keyword_score: float = 0.0,
    combined_score: float = 0.0,
    temporal_decay_factor: float = 1.0,
    timestamp: datetime | None = None,
):
    return SearchRankingRecord(
        turn_id=turn_id,
        room_id=room_id,
        content=content,
        vector_score=vector_score,
        keyword_score=keyword_score,
        combined_score=combined_score,
        temporal_decay_factor=temporal_decay_factor,
        timestamp=timestamp,
    )


@pytest.fixture
def sample_vector_results():
    return [
        record("turn-1", vector_score=0.95, timestamp=NOW - timedelta(days=1)),
        record("turn-2", vector_score=0.80, timestamp=NOW - timedelta(days=5)),
        record("turn-3", vector_score=0.60, timestamp=NOW - timedelta(days=30)),
    ]


@pytest.fixture
def sample_keyword_results():
    return [
        record(
            "turn-2",
            content="keyword match content",
            keyword_score=5.0,
            timestamp=NOW - timedelta(days=5),
        ),
        record(
            "turn-4",
            content="another keyword match",
            keyword_score=3.0,
            timestamp=NOW - timedelta(days=10),
        ),
    ]


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert search.cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert search.cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert search.cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self):
        assert search.cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0

    def test_known_angle(self):
        result = search.cosine_similarity([1, 0], [1, 1])
        expected = 1 / math.sqrt(2)
        assert result == pytest.approx(expected, rel=1e-6)


class TestMergeResults:
    def test_merge_disjoint_results(
        self, sample_vector_results, sample_keyword_results
    ):
        merged = search.merge_results(
            sample_vector_results,
            sample_keyword_results,
            vector_weight=0.7,
            keyword_weight=0.3,
        )
        turn_ids = [r.turn_id for r in merged]
        assert "turn-1" in turn_ids
        assert "turn-4" in turn_ids

    def test_merge_overlapping_turn(
        self, sample_vector_results, sample_keyword_results
    ):
        merged = search.merge_results(
            sample_vector_results,
            sample_keyword_results,
            vector_weight=0.7,
            keyword_weight=0.3,
        )
        turn2 = next(r for r in merged if r.turn_id == "turn-2")
        assert turn2.vector_score > 0
        assert turn2.keyword_score > 0
        assert turn2.combined_score == pytest.approx(
            0.7 * turn2.vector_score + 0.3 * turn2.keyword_score
        )

    def test_merge_empty_vector_results(self, sample_keyword_results):
        merged = search.merge_results(
            [], sample_keyword_results, vector_weight=0.7, keyword_weight=0.3
        )
        assert len(merged) == len(sample_keyword_results)
        for result in merged:
            assert result.vector_score == 0.0

    def test_merge_empty_keyword_results(self, sample_vector_results):
        merged = search.merge_results(
            sample_vector_results, [], vector_weight=0.7, keyword_weight=0.3
        )
        assert len(merged) == len(sample_vector_results)
        for result in merged:
            assert result.keyword_score == 0.0

    def test_merge_both_empty(self):
        merged = search.merge_results(
            [], [], vector_weight=0.7, keyword_weight=0.3
        )
        assert merged == []

    def test_merge_sorted_by_combined_score(
        self, sample_vector_results, sample_keyword_results
    ):
        merged = search.merge_results(
            sample_vector_results,
            sample_keyword_results,
            vector_weight=0.7,
            keyword_weight=0.3,
        )
        scores = [r.combined_score for r in merged]
        assert scores == sorted(scores, reverse=True)

    def test_merge_normalizes_scores(self):
        vec = [
            record("a", room_id="r", vector_score=100.0),
            record("b", room_id="r", vector_score=50.0),
        ]
        merged = search.merge_results(
            vec, [], vector_weight=1.0, keyword_weight=0.0
        )
        a = next(r for r in merged if r.turn_id == "a")
        b = next(r for r in merged if r.turn_id == "b")
        assert a.vector_score == pytest.approx(1.0)
        assert b.vector_score == pytest.approx(0.5)


class TestTemporalDecay:
    def test_recent_result_decays_less(self):
        results = [
            record(
                "recent",
                room_id="r",
                combined_score=1.0,
                timestamp=NOW - timedelta(days=1),
            ),
            record(
                "old",
                room_id="r",
                combined_score=1.0,
                timestamp=NOW - timedelta(days=60),
            ),
        ]
        decayed = search.apply_temporal_decay(results, half_life_days=30)
        recent = next(r for r in decayed if r.turn_id == "recent")
        old = next(r for r in decayed if r.turn_id == "old")
        assert recent.combined_score > old.combined_score

    def test_half_life_halves_score(self):
        results = [
            record(
                "t",
                room_id="r",
                combined_score=1.0,
                timestamp=NOW - timedelta(days=30),
            ),
        ]
        decayed = search.apply_temporal_decay(results, half_life_days=30)
        assert decayed[0].combined_score == pytest.approx(0.5, rel=0.01)
        assert decayed[0].temporal_decay_factor == pytest.approx(0.5, rel=0.01)

    def test_zero_age_no_decay(self):
        results = [record("t", room_id="r", combined_score=1.0, timestamp=NOW)]
        decayed = search.apply_temporal_decay(results, half_life_days=30)
        assert decayed[0].combined_score == pytest.approx(1.0, rel=0.01)

    def test_no_timestamp_gets_penalty(self):
        results = [record("t", room_id="r", combined_score=1.0, timestamp=None)]
        decayed = search.apply_temporal_decay(results, half_life_days=30)
        assert decayed[0].temporal_decay_factor == 0.5

    def test_zero_half_life_returns_unchanged(self):
        results = [record("t", room_id="r", combined_score=1.0, timestamp=None)]
        decayed = search.apply_temporal_decay(results, half_life_days=0)
        assert decayed[0].combined_score == 1.0

    def test_decay_re_sorts_results(self):
        results = [
            record(
                "high-old",
                room_id="r",
                combined_score=1.0,
                timestamp=NOW - timedelta(days=120),
            ),
            record(
                "low-new",
                room_id="r",
                combined_score=0.5,
                timestamp=NOW - timedelta(days=1),
            ),
        ]
        decayed = search.apply_temporal_decay(results, half_life_days=30)
        assert decayed[0].turn_id == "low-new"


class TestMMR:
    def test_single_result_unchanged(self):
        results = [
            record(
                "t",
                room_id="r",
                combined_score=1.0,
                vector_score=1.0,
                temporal_decay_factor=1.0,
            ),
        ]
        reranked = search.apply_mmr(results, lambda_param=0.7)
        assert len(reranked) == 1
        assert reranked[0].turn_id == "t"

    def test_empty_results(self):
        assert search.apply_mmr([], lambda_param=0.7) == []

    def test_first_pick_is_highest_score(self):
        results = [
            record("low", room_id="r", combined_score=0.3, vector_score=0.3),
            record("high", room_id="r", combined_score=0.9, vector_score=0.9),
        ]
        reranked = search.apply_mmr(results, lambda_param=0.7)
        assert reranked[0].turn_id == "high"

    def test_pure_relevance_preserves_order(self):
        results = [
            record(
                f"t{i}",
                room_id="r",
                combined_score=float(i) / 5,
                vector_score=float(i) / 5,
                temporal_decay_factor=1.0,
            )
            for i in range(5, 0, -1)
        ]
        reranked = search.apply_mmr(results, lambda_param=1.0)
        scores = [r.combined_score for r in reranked]
        assert scores == sorted(scores, reverse=True)

    def test_diversity_mode_promotes_diverse_results(self):
        results = [
            record(
                "vec-only",
                room_id="r",
                combined_score=0.8,
                vector_score=1.0,
                temporal_decay_factor=1.0,
            ),
            record(
                "vec-clone",
                room_id="r",
                combined_score=0.79,
                vector_score=0.99,
                temporal_decay_factor=1.0,
            ),
            record(
                "kw-only",
                room_id="r",
                combined_score=0.5,
                keyword_score=1.0,
                temporal_decay_factor=1.0,
            ),
        ]
        reranked = search.apply_mmr(results, lambda_param=0.0)
        assert reranked[0].turn_id == "vec-only"
        assert reranked[1].turn_id == "kw-only"

    def test_all_results_included(self):
        results = [
            record(
                f"t{i}",
                room_id="r",
                combined_score=0.5,
                vector_score=0.5,
                keyword_score=0.5,
                temporal_decay_factor=1.0,
            )
            for i in range(5)
        ]
        reranked = search.apply_mmr(results, lambda_param=0.7)
        assert len(reranked) == 5


class TestVectorKeywordSearch:
    @pytest.mark.asyncio
    async def test_vector_search_embeds_and_queries_room_index(self):
        llm = FakeLLM(embedding=[0.2] * 3)
        vector = FakeVector(
            [
                VectorSearchResult(
                    id="turn-abc",
                    score=0.95,
                    metadata={
                        "turn_id": "turn-abc",
                        "content_preview": "vector preview",
                        "timestamp": NOW.isoformat(),
                    },
                )
            ]
        )

        results = await search.vector_search(
            room_id="room-1",
            query="query",
            vector=vector,
            llm_provider=llm,
            config=config(index_name="room-memory"),
        )

        assert results[0].turn_id == "turn-abc"
        assert results[0].content == "vector preview"
        assert results[0].metadata["can_expand"] is True
        assert vector.search_calls == [
            ("room-memory", [0.2] * 3, 50, {"room_id": {"$eq": "room-1"}})
        ]

    @pytest.mark.asyncio
    async def test_keyword_search_uses_turn_notes_preview(self):
        repo = FakeContentRepository(
            text_results=[
                {
                    "turn_id": "turn-1",
                    "score": 5.0,
                    "turn_notes": {"one_liner": "keyword preview"},
                    "content_type": "text",
                    "stored_at": NOW,
                }
            ]
        )

        results = await search.keyword_search(
            room_id="room-1",
            query="keyword",
            content_repository=repo,
            config=config(),
        )

        assert results[0].turn_id == "turn-1"
        assert results[0].content == "keyword preview"
        assert results[0].keyword_score == 5.0
        assert repo.text_calls == [("room-1", "keyword", 50)]


class TestIndexing:
    @pytest.mark.asyncio
    async def test_index_turn_embeds_and_upserts(self):
        llm = FakeLLM(embedding=[0.3] * 3)
        vector = FakeVector()

        result = await search.index_turn_for_search(
            room_id="room-1",
            turn_doc={
                "turn_id": "turn-abc",
                "role": "user",
                "agent_name": "Agent",
                "content": "Hello world test content",
                "timestamp": NOW,
            },
            vector=vector,
            llm_provider=llm,
            config=config(index_name="room-memory"),
        )

        assert result is True
        assert llm.embedded == ["Hello world test content"]
        record = vector.upserted[0][1][0]
        assert record.id == "turn-abc"
        assert record.vector == [0.3] * 3
        assert record.metadata["room_id"] == "room-1"

    @pytest.mark.asyncio
    async def test_index_turn_skips_empty_content(self):
        llm = FakeLLM()

        result = await search.index_turn_for_search(
            room_id="room-1",
            turn_doc={"turn_id": "turn-empty", "role": "user", "content": ""},
            vector=FakeVector(),
            llm_provider=llm,
            config=config(),
        )

        assert result is False
        assert llm.embedded == []

    @pytest.mark.asyncio
    async def test_index_turn_handles_embedding_failure(self):
        class FailingLLM:
            async def embed(self, text: str):
                raise RuntimeError("API error")

        result = await search.index_turn_for_search(
            room_id="room-1",
            turn_doc={"turn_id": "turn-fail", "role": "user", "content": "content"},
            vector=FakeVector(),
            llm_provider=FailingLLM(),
            config=config(),
        )

        assert result is False


class TestSearchPipeline:
    @pytest.mark.asyncio
    async def test_search_disabled_returns_empty(self):
        results, response = await search.search_memory(
            room_id="room-1",
            query="test query",
            limit=10,
            vector=FakeVector(),
            llm_provider=FakeLLM(),
            content_repository=FakeContentRepository(),
            config=config(enabled=False),
        )

        assert results == []
        assert response["results"] == []
        assert response["vector_search_used"] is False

    @pytest.mark.asyncio
    async def test_search_combines_vector_and_keyword(self):
        vector = FakeVector(
            [
                SimpleNamespace(
                    id="turn-1",
                    score=0.95,
                    metadata={
                        "turn_id": "turn-1",
                        "content_preview": "vector preview",
                        "timestamp": NOW.isoformat(),
                    },
                )
            ]
        )
        repo = FakeContentRepository(
            text_results=[
                {
                    "turn_id": "turn-2",
                    "score": 5.0,
                    "turn_notes": {"one_liner": "keyword match content"},
                    "content_type": "text",
                    "stored_at": NOW,
                }
            ]
        )

        results, response = await search.search_memory(
            room_id="room-1",
            query="test",
            limit=10,
            vector=vector,
            llm_provider=FakeLLM(),
            content_repository=repo,
            config=config(),
        )

        assert len(results) == 2
        assert response["vector_search_used"] is True
        assert response["keyword_search_used"] is True
        assert response["temporal_decay_applied"] is True
        assert response["mmr_applied"] is True
        assert response["search_time_ms"] >= 0

    @pytest.mark.asyncio
    async def test_search_graceful_on_vector_failure(self):
        results, response = await search.search_memory(
            room_id="room-1",
            query="test",
            limit=10,
            vector=RaisingVector(),
            llm_provider=FakeLLM(),
            content_repository=FakeContentRepository(
                text_results=[
                    {
                        "turn_id": "turn-2",
                        "score": 5.0,
                        "turn_notes": {"one_liner": "keyword match content"},
                        "content_type": "text",
                        "stored_at": NOW,
                    }
                ]
            ),
            config=config(),
        )

        assert response["vector_search_used"] is False
        assert response["keyword_search_used"] is True
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_search_graceful_on_keyword_failure(self):
        results, response = await search.search_memory(
            room_id="room-1",
            query="test",
            limit=10,
            vector=FakeVector(
                [
                    SimpleNamespace(
                        id="turn-1",
                        score=0.95,
                        metadata={"turn_id": "turn-1", "content_preview": "vector"},
                    )
                ]
            ),
            llm_provider=FakeLLM(),
            content_repository=RaisingContentRepository(),
            config=config(),
        )

        assert response["vector_search_used"] is True
        assert response["keyword_search_used"] is False
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_search_both_fail_returns_empty(self):
        results, response = await search.search_memory(
            room_id="room-1",
            query="test",
            limit=10,
            vector=RaisingVector(),
            llm_provider=FakeLLM(),
            content_repository=RaisingContentRepository(),
            config=config(),
        )

        assert results == []
        assert response["results"] == []
        assert response["vector_search_used"] is False
        assert response["keyword_search_used"] is False

    @pytest.mark.asyncio
    async def test_search_respects_configured_max_results(self):
        vector = FakeVector(
            [
                SimpleNamespace(
                    id=f"t{i}",
                    score=float(10 - i),
                    metadata={"turn_id": f"t{i}", "content_preview": f"result {i}"},
                )
                for i in range(10)
            ]
        )

        results, response = await search.search_memory(
            room_id="room-1",
            query="test",
            limit=None,
            vector=vector,
            llm_provider=FakeLLM(),
            content_repository=FakeContentRepository(),
            config=config(max_results=3),
        )

        assert len(results) == 3
        assert len(response["results"]) == 3


class TestPineconeMultiIndex:
    def test_get_index_caches(self):
        from dal.pinecone.client import VectorDALImpl

        class FakePinecone:
            def __init__(self):
                self.index_calls = []
                self.index = object()

            def Index(self, name):
                self.index_calls.append(name)
                return self.index

        fake_pc = FakePinecone()
        vector = VectorDALImpl(client=fake_pc)

        idx1 = vector._get_index("test-index")
        idx2 = vector._get_index("test-index")

        assert idx1 is idx2
        assert fake_pc.index_calls == ["test-index"]

    def test_get_index_different_names(self):
        from dal.pinecone.client import VectorDALImpl

        class FakePinecone:
            def __init__(self):
                self.index_calls = []

            def Index(self, name):
                self.index_calls.append(name)
                return object()

        fake_pc = FakePinecone()
        vector = VectorDALImpl(client=fake_pc)

        vector._get_index("index-a")
        vector._get_index("index-b")

        assert fake_pc.index_calls == ["index-a", "index-b"]


class TestMemorySearchConfig:
    def test_index_name_reads_settings(self, monkeypatch):
        from context_memory import config as config_module

        monkeypatch.setattr(
            config_module, "_setting", lambda name, fallback: "settings-room-memory"
        )

        assert MemorySearchConfig().index_name == "settings-room-memory"

    def test_blank_index_name_falls_back_to_default(self):
        from common.config import MEMORY_SEARCH_INDEX_NAME_DEFAULT, Settings

        settings = Settings(_env_file=None, memory_search_index_name="")

        assert settings.memory_search_index_name == MEMORY_SEARCH_INDEX_NAME_DEFAULT
