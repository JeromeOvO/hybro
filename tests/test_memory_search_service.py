"""
Unit tests for Memory Search Service.

Tests cover:
- Vector search (Pinecone mock)
- Keyword search (MongoDB mock)
- Result merging with weighted scoring
- Temporal decay with configurable half-life
- MMR re-ranking with hand-crafted vectors
- Turn indexing (write path)
- Graceful degradation on failures
- End-to-end search pipeline

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §8 for design specification.
"""

import math
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from uuid import uuid4

from models.memory import ConversationTurn, ContentType, TurnRepresentation, TurnRole
from models.search import MemorySearchResult, MemorySourceType
from services.memory_search_service import (
    MemorySearchService,
    _cosine_similarity,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_settings():
    """Mock settings for memory search configuration."""
    with patch("models.context_config.settings") as mock:
        mock.memory_search_enabled = True
        mock.memory_search_vector_weight = 0.7
        mock.memory_search_keyword_weight = 0.3
        mock.memory_search_temporal_decay_enabled = True
        mock.memory_search_half_life_days = 30
        mock.memory_search_mmr_lambda = 0.7
        mock.memory_search_max_results = 10
        mock.memory_search_max_snippet_chars = 500
        mock.memory_search_index_name = "room-memory"
        yield mock


@pytest.fixture
def service():
    """Create a MemorySearchService with mocked dependencies."""
    svc = MemorySearchService()
    svc.openai_service = MagicMock()
    svc.openai_service.get_embedding = AsyncMock(return_value=[0.1] * 1536)
    svc._index_available = True
    return svc


@pytest.fixture
def sample_vector_results():
    """Sample results from vector search."""
    now = datetime.now(timezone.utc)
    return [
        MemorySearchResult(
            turn_id="turn-1",
            room_id="room-1",
            source_type=MemorySourceType.TURN,
            content="",
            vector_score=0.95,
            timestamp=now - timedelta(days=1),
        ),
        MemorySearchResult(
            turn_id="turn-2",
            room_id="room-1",
            source_type=MemorySourceType.TURN,
            content="",
            vector_score=0.80,
            timestamp=now - timedelta(days=5),
        ),
        MemorySearchResult(
            turn_id="turn-3",
            room_id="room-1",
            source_type=MemorySourceType.TURN,
            content="",
            vector_score=0.60,
            timestamp=now - timedelta(days=30),
        ),
    ]


@pytest.fixture
def sample_keyword_results():
    """Sample results from keyword search."""
    now = datetime.now(timezone.utc)
    return [
        MemorySearchResult(
            turn_id="turn-2",
            room_id="room-1",
            source_type=MemorySourceType.TURN,
            content="keyword match content",
            keyword_score=5.0,
            timestamp=now - timedelta(days=5),
        ),
        MemorySearchResult(
            turn_id="turn-4",
            room_id="room-1",
            source_type=MemorySourceType.TURN,
            content="another keyword match",
            keyword_score=3.0,
            timestamp=now - timedelta(days=10),
        ),
    ]


# =============================================================================
# Cosine Similarity Tests
# =============================================================================


class TestCosineSimilarity:
    """Tests for the _cosine_similarity utility."""

    def test_identical_vectors(self):
        assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self):
        assert _cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0

    def test_known_angle(self):
        """45-degree angle should give cos(45°) ≈ 0.707."""
        result = _cosine_similarity([1, 0], [1, 1])
        expected = 1 / math.sqrt(2)
        assert result == pytest.approx(expected, rel=1e-6)


# =============================================================================
# Result Merging Tests
# =============================================================================


class TestMergeResults:
    """Tests for _merge_results weighted combination."""

    def test_merge_disjoint_results(
        self, sample_vector_results, sample_keyword_results
    ):
        """Disjoint results should appear with single-source scores."""
        merged = MemorySearchService._merge_results(
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
        """turn-2 appears in both; should have both scores combined."""
        merged = MemorySearchService._merge_results(
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
        merged = MemorySearchService._merge_results(
            [], sample_keyword_results, vector_weight=0.7, keyword_weight=0.3
        )
        assert len(merged) == len(sample_keyword_results)
        for r in merged:
            assert r.vector_score == 0.0

    def test_merge_empty_keyword_results(self, sample_vector_results):
        merged = MemorySearchService._merge_results(
            sample_vector_results, [], vector_weight=0.7, keyword_weight=0.3
        )
        assert len(merged) == len(sample_vector_results)
        for r in merged:
            assert r.keyword_score == 0.0

    def test_merge_both_empty(self):
        merged = MemorySearchService._merge_results(
            [], [], vector_weight=0.7, keyword_weight=0.3
        )
        assert merged == []

    def test_merge_sorted_by_combined_score(
        self, sample_vector_results, sample_keyword_results
    ):
        merged = MemorySearchService._merge_results(
            sample_vector_results,
            sample_keyword_results,
            vector_weight=0.7,
            keyword_weight=0.3,
        )
        scores = [r.combined_score for r in merged]
        assert scores == sorted(scores, reverse=True)

    def test_merge_normalizes_scores(self):
        """Scores should be normalized to [0,1] within each source."""
        vec = [
            MemorySearchResult(
                turn_id="a", room_id="r", source_type=MemorySourceType.TURN,
                content="", vector_score=100.0,
            ),
            MemorySearchResult(
                turn_id="b", room_id="r", source_type=MemorySourceType.TURN,
                content="", vector_score=50.0,
            ),
        ]
        merged = MemorySearchService._merge_results(
            vec, [], vector_weight=1.0, keyword_weight=0.0
        )
        a = next(r for r in merged if r.turn_id == "a")
        b = next(r for r in merged if r.turn_id == "b")
        assert a.vector_score == pytest.approx(1.0)
        assert b.vector_score == pytest.approx(0.5)


# =============================================================================
# Temporal Decay Tests
# =============================================================================


class TestTemporalDecay:
    """Tests for _apply_temporal_decay."""

    def test_recent_result_decays_less(self):
        now = datetime.now(timezone.utc)
        results = [
            MemorySearchResult(
                turn_id="recent", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=1.0, timestamp=now - timedelta(days=1),
            ),
            MemorySearchResult(
                turn_id="old", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=1.0, timestamp=now - timedelta(days=60),
            ),
        ]
        decayed = MemorySearchService._apply_temporal_decay(results, half_life_days=30)
        recent = next(r for r in decayed if r.turn_id == "recent")
        old = next(r for r in decayed if r.turn_id == "old")
        assert recent.combined_score > old.combined_score

    def test_half_life_halves_score(self):
        now = datetime.now(timezone.utc)
        results = [
            MemorySearchResult(
                turn_id="t", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=1.0,
                timestamp=now - timedelta(days=30),
            ),
        ]
        decayed = MemorySearchService._apply_temporal_decay(results, half_life_days=30)
        assert decayed[0].combined_score == pytest.approx(0.5, rel=0.01)
        assert decayed[0].temporal_decay_factor == pytest.approx(0.5, rel=0.01)

    def test_zero_age_no_decay(self):
        now = datetime.now(timezone.utc)
        results = [
            MemorySearchResult(
                turn_id="t", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=1.0, timestamp=now,
            ),
        ]
        decayed = MemorySearchService._apply_temporal_decay(results, half_life_days=30)
        assert decayed[0].combined_score == pytest.approx(1.0, rel=0.01)

    def test_no_timestamp_gets_penalty(self):
        results = [
            MemorySearchResult(
                turn_id="t", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=1.0, timestamp=None,
            ),
        ]
        decayed = MemorySearchService._apply_temporal_decay(results, half_life_days=30)
        assert decayed[0].temporal_decay_factor == 0.5

    def test_zero_half_life_returns_unchanged(self):
        results = [
            MemorySearchResult(
                turn_id="t", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=1.0, timestamp=None,
            ),
        ]
        decayed = MemorySearchService._apply_temporal_decay(results, half_life_days=0)
        assert decayed[0].combined_score == 1.0

    def test_decay_re_sorts_results(self):
        """An older result with higher base score may rank below a newer lower-score result."""
        now = datetime.now(timezone.utc)
        results = [
            MemorySearchResult(
                turn_id="high-old", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=1.0,
                timestamp=now - timedelta(days=120),
            ),
            MemorySearchResult(
                turn_id="low-new", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=0.5,
                timestamp=now - timedelta(days=1),
            ),
        ]
        decayed = MemorySearchService._apply_temporal_decay(results, half_life_days=30)
        assert decayed[0].turn_id == "low-new"


# =============================================================================
# MMR Tests
# =============================================================================


class TestMMR:
    """Tests for _apply_mmr Maximal Marginal Relevance."""

    def test_single_result_unchanged(self):
        results = [
            MemorySearchResult(
                turn_id="t", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=1.0, vector_score=1.0,
                keyword_score=0.0, temporal_decay_factor=1.0,
            ),
        ]
        reranked = MemorySearchService._apply_mmr(results, lambda_param=0.7)
        assert len(reranked) == 1
        assert reranked[0].turn_id == "t"

    def test_empty_results(self):
        assert MemorySearchService._apply_mmr([], lambda_param=0.7) == []

    def test_first_pick_is_highest_score(self):
        results = [
            MemorySearchResult(
                turn_id="low", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=0.3, vector_score=0.3,
                keyword_score=0.0, temporal_decay_factor=1.0,
            ),
            MemorySearchResult(
                turn_id="high", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=0.9, vector_score=0.9,
                keyword_score=0.0, temporal_decay_factor=1.0,
            ),
        ]
        reranked = MemorySearchService._apply_mmr(results, lambda_param=0.7)
        assert reranked[0].turn_id == "high"

    def test_pure_relevance_preserves_order(self):
        """lambda=1.0 should give pure relevance ordering."""
        results = [
            MemorySearchResult(
                turn_id=f"t{i}", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=float(i) / 5,
                vector_score=float(i) / 5,
                keyword_score=0.0, temporal_decay_factor=1.0,
            )
            for i in range(5, 0, -1)
        ]
        reranked = MemorySearchService._apply_mmr(results, lambda_param=1.0)
        scores = [r.combined_score for r in reranked]
        assert scores == sorted(scores, reverse=True)

    def test_diversity_mode_promotes_diverse_results(self):
        """lambda=0.0 should promote diversity (different score profiles)."""
        results = [
            MemorySearchResult(
                turn_id="vec-only", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=0.8,
                vector_score=1.0, keyword_score=0.0, temporal_decay_factor=1.0,
            ),
            MemorySearchResult(
                turn_id="vec-clone", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=0.79,
                vector_score=0.99, keyword_score=0.0, temporal_decay_factor=1.0,
            ),
            MemorySearchResult(
                turn_id="kw-only", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=0.5,
                vector_score=0.0, keyword_score=1.0, temporal_decay_factor=1.0,
            ),
        ]
        reranked = MemorySearchService._apply_mmr(results, lambda_param=0.0)
        # "kw-only" is maximally diverse from "vec-only", should appear 2nd
        assert reranked[0].turn_id == "vec-only"
        assert reranked[1].turn_id == "kw-only"

    def test_all_results_included(self):
        results = [
            MemorySearchResult(
                turn_id=f"t{i}", room_id="r",
                source_type=MemorySourceType.TURN, content="",
                combined_score=0.5,
                vector_score=0.5, keyword_score=0.5, temporal_decay_factor=1.0,
            )
            for i in range(5)
        ]
        reranked = MemorySearchService._apply_mmr(results, lambda_param=0.7)
        assert len(reranked) == 5


# =============================================================================
# Indexing (Write Path) Tests
# =============================================================================


class TestIndexing:
    """Tests for index_turn_for_search write path."""

    @pytest.fixture
    def service_with_mocked_pinecone(self, service):
        mock_index = MagicMock()
        mock_index.upsert = MagicMock()
        with patch.object(
            type(service), "_pinecone_index",
            new_callable=PropertyMock, return_value=mock_index,
        ):
            service._mock_index = mock_index
            yield service

    @pytest.mark.asyncio
    async def test_index_turn_embeds_and_upserts(
        self, service_with_mocked_pinecone, mock_settings
    ):
        svc = service_with_mocked_pinecone
        turn = ConversationTurn(
            turn_id="turn-abc",
            role=TurnRole.USER,
            content="Hello world test content",
            content_type=ContentType.TEXT,
            representation=TurnRepresentation.FULL,
        )
        result = await svc.index_turn_for_search(turn, "room-1")

        assert result is True
        svc.openai_service.get_embedding.assert_called_once_with(
            "Hello world test content"
        )
        svc._mock_index.upsert.assert_called_once()
        call_args = svc._mock_index.upsert.call_args
        vectors = call_args[1]["vectors"]
        assert vectors[0]["id"] == "turn-abc"
        assert vectors[0]["metadata"]["room_id"] == "room-1"

    @pytest.mark.asyncio
    async def test_index_turn_skips_empty_content(self, service, mock_settings):
        turn = ConversationTurn(
            turn_id="turn-empty",
            role=TurnRole.USER,
            content=None,
            content_type=ContentType.TEXT,
            representation=TurnRepresentation.COMPACT,
        )
        result = await service.index_turn_for_search(turn, "room-1")
        assert result is False
        service.openai_service.get_embedding.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_turn_handles_embedding_failure(
        self, service, mock_settings
    ):
        service.openai_service.get_embedding = AsyncMock(
            side_effect=Exception("API error")
        )
        turn = ConversationTurn(
            turn_id="turn-fail",
            role=TurnRole.USER,
            content="content",
            content_type=ContentType.TEXT,
        )
        result = await service.index_turn_for_search(turn, "room-1")
        assert result is False


# =============================================================================
# End-to-End Search Pipeline Tests
# =============================================================================


class TestSearchPipeline:
    """Tests for the full search() method."""

    @pytest.mark.asyncio
    async def test_search_disabled_returns_empty(self, service, mock_settings):
        mock_settings.memory_search_enabled = False
        response = await service.search("test query", "room-1")
        assert response.results == []
        assert response.vector_search_used is False

    @pytest.mark.asyncio
    async def test_search_combines_vector_and_keyword(
        self, service, mock_settings, sample_vector_results, sample_keyword_results
    ):
        """Full pipeline should merge, decay, and MMR results."""
        with patch.object(
            service, "_vector_search",
            new=AsyncMock(return_value=sample_vector_results),
        ):
            with patch.object(
                service, "_keyword_search",
                new=AsyncMock(return_value=sample_keyword_results),
            ):
                response = await service.search("test", "room-1")

        assert len(response.results) > 0
        assert response.vector_search_used is True
        assert response.keyword_search_used is True
        assert response.temporal_decay_applied is True
        assert response.mmr_applied is True
        assert response.search_time_ms > 0

    @pytest.mark.asyncio
    async def test_search_graceful_on_vector_failure(
        self, service, mock_settings, sample_keyword_results
    ):
        """Should return keyword results even if vector search fails."""
        with patch.object(
            service, "_vector_search",
            new=AsyncMock(side_effect=Exception("Pinecone down")),
        ):
            with patch.object(
                service, "_keyword_search",
                new=AsyncMock(return_value=sample_keyword_results),
            ):
                response = await service.search("test", "room-1")

        assert response.vector_search_used is False
        assert response.keyword_search_used is True
        assert len(response.results) > 0

    @pytest.mark.asyncio
    async def test_search_graceful_on_keyword_failure(
        self, service, mock_settings, sample_vector_results
    ):
        """Should return vector results even if keyword search fails."""
        with patch.object(
            service, "_vector_search",
            new=AsyncMock(return_value=sample_vector_results),
        ):
            with patch.object(
                service, "_keyword_search",
                new=AsyncMock(side_effect=Exception("MongoDB down")),
            ):
                response = await service.search("test", "room-1")

        assert response.vector_search_used is True
        assert response.keyword_search_used is False
        assert len(response.results) > 0

    @pytest.mark.asyncio
    async def test_search_both_fail_returns_empty(self, service, mock_settings):
        """Should return empty results if both backends fail."""
        with patch.object(
            service, "_vector_search",
            new=AsyncMock(side_effect=Exception("fail")),
        ):
            with patch.object(
                service, "_keyword_search",
                new=AsyncMock(side_effect=Exception("fail")),
            ):
                response = await service.search("test", "room-1")

        assert response.results == []
        assert response.vector_search_used is False
        assert response.keyword_search_used is False

    @pytest.mark.asyncio
    async def test_search_respects_max_results(self, service, mock_settings):
        mock_settings.memory_search_max_results = 3
        many_results = [
            MemorySearchResult(
                turn_id=f"t{i}", room_id="room-1",
                source_type=MemorySourceType.TURN, content="",
                vector_score=float(i) / 10,
                timestamp=datetime.now(timezone.utc),
            )
            for i in range(10)
        ]
        with patch.object(
            service, "_vector_search",
            new=AsyncMock(return_value=many_results),
        ):
            with patch.object(
                service, "_keyword_search",
                new=AsyncMock(return_value=[]),
            ):
                response = await service.search("test", "room-1")

        assert len(response.results) <= 3


# =============================================================================
# PineconeDB Multi-Index Tests
# =============================================================================


class TestPineconeMultiIndex:
    """Tests for PineconeDB.get_index() multi-index support."""

    def test_get_index_caches(self):
        from database.pinecone_db import PineconeDB

        db = PineconeDB()
        mock_pc = MagicMock()
        mock_index = MagicMock()
        mock_pc.Index.return_value = mock_index
        db._pc = mock_pc

        idx1 = db.get_index("test-index")
        idx2 = db.get_index("test-index")

        assert idx1 is idx2
        mock_pc.Index.assert_called_once_with("test-index")

    def test_get_index_different_names(self):
        from database.pinecone_db import PineconeDB

        db = PineconeDB()
        mock_pc = MagicMock()
        db._pc = mock_pc

        db.get_index("index-a")
        db.get_index("index-b")

        assert mock_pc.Index.call_count == 2


# =============================================================================
# Context Config Tests
# =============================================================================


class TestMemorySearchConfig:
    """Tests for MemorySearchConfig from context_config.py."""

    def test_reads_from_settings(self, mock_settings):
        from models.context_config import memory_search_config

        assert memory_search_config.enabled is True
        assert memory_search_config.vector_weight == 0.7
        assert memory_search_config.keyword_weight == 0.3
        assert memory_search_config.half_life_days == 30
        assert memory_search_config.mmr_lambda == 0.7
        assert memory_search_config.max_results == 10
        assert memory_search_config.index_name == "room-memory"

    def test_disabled_when_setting_false(self, mock_settings):
        mock_settings.memory_search_enabled = False
        from models.context_config import memory_search_config

        assert memory_search_config.enabled is False


# =========================================================================
# Test: _vector_search handles PineconeNotFoundException gracefully
# =========================================================================


class TestVectorSearchPineconeNotFound:
    """Regression tests for Pinecone 404 (index not found) handling.

    When the Pinecone index (e.g. 'room-memory') doesn't exist, the SDK
    raises NotFoundException. The service should return an empty list
    instead of propagating the exception.
    """

    @pytest.fixture
    def service(self):
        from services.memory_search_service import MemorySearchService
        svc = MemorySearchService()
        svc.openai_service = MagicMock()
        svc.openai_service.get_embedding = AsyncMock(return_value=[0.1] * 1536)
        svc._index_available = None
        return svc

    @pytest.mark.asyncio
    async def test_returns_empty_on_not_found(self, service):
        """_vector_search should return [] when Pinecone index doesn't exist."""
        from pinecone.exceptions import NotFoundException

        mock_index = MagicMock()
        mock_index.describe_index_stats.side_effect = NotFoundException(
            "Resource room-memory not found"
        )
        mock_index.query.side_effect = NotFoundException("Resource room-memory not found")
        service.pinecone = MagicMock()
        service.pinecone.get_index = MagicMock(return_value=mock_index)

        results = await service._vector_search("test query", "room-1")
        assert results == []

    @pytest.mark.asyncio
    async def test_not_found_does_not_propagate(self, service):
        """NotFoundException should not bubble up to the caller."""
        from pinecone.exceptions import NotFoundException

        mock_index = MagicMock()
        mock_index.describe_index_stats.side_effect = NotFoundException("Not found")
        mock_index.query.side_effect = NotFoundException("Not found")
        service.pinecone = MagicMock()
        service.pinecone.get_index = MagicMock(return_value=mock_index)

        try:
            results = await service._vector_search("query", "room-1")
        except NotFoundException:
            pytest.fail("NotFoundException should be caught, not propagated")

        assert results == []

    @pytest.mark.asyncio
    async def test_other_exceptions_still_propagate(self, service):
        """Transient errors return [] but do NOT permanently cache unavailability."""
        mock_index = MagicMock()
        mock_index.describe_index_stats.side_effect = ConnectionError("Pinecone unreachable")
        service.pinecone = MagicMock()
        service.pinecone.get_index = MagicMock(return_value=mock_index)

        results = await service._vector_search("query", "room-1")
        assert results == []
        assert service._index_available is None


# =========================================================================
# Test: Index availability check caches result and skips embedding calls
# =========================================================================


class TestIndexAvailabilityCheck:
    """Tests for _is_index_available() caching and early-return behavior."""

    @pytest.fixture
    def service(self):
        from services.memory_search_service import MemorySearchService
        svc = MemorySearchService()
        svc.openai_service = MagicMock()
        svc.openai_service.get_embedding = AsyncMock(return_value=[0.1] * 1536)
        svc._index_available = None
        return svc

    def test_caches_true_on_success(self, service):
        mock_index = MagicMock()
        mock_index.describe_index_stats.return_value = {}
        service.pinecone = MagicMock()
        service.pinecone.get_index = MagicMock(return_value=mock_index)

        assert service._is_index_available() is True
        assert service._index_available is True
        # Second call should not probe again
        service._is_index_available()
        mock_index.describe_index_stats.assert_called_once()

    def test_caches_false_on_not_found(self, service):
        from pinecone.exceptions import NotFoundException

        mock_index = MagicMock()
        mock_index.describe_index_stats.side_effect = NotFoundException("Not found")
        service.pinecone = MagicMock()
        service.pinecone.get_index = MagicMock(return_value=mock_index)

        assert service._is_index_available() is False
        assert service._index_available is False
        service._is_index_available()
        mock_index.describe_index_stats.assert_called_once()

    def test_does_not_cache_on_generic_error(self, service):
        """Transient errors should NOT be cached — next call retries."""
        mock_index = MagicMock()
        mock_index.describe_index_stats.side_effect = ConnectionError("timeout")
        service.pinecone = MagicMock()
        service.pinecone.get_index = MagicMock(return_value=mock_index)

        assert service._is_index_available() is False
        assert service._index_available is None

        mock_index.describe_index_stats.side_effect = None
        mock_index.describe_index_stats.return_value = {}
        assert service._is_index_available() is True
        assert service._index_available is True

    @pytest.mark.asyncio
    async def test_vector_search_skips_embedding_when_unavailable(self, service):
        """When index is unavailable, _vector_search should return []
        without calling get_embedding (saving the OpenAI API call)."""
        service._index_available = False

        results = await service._vector_search("query", "room-1")

        assert results == []
        service.openai_service.get_embedding.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_turn_skips_embedding_when_unavailable(self, service):
        """When index is unavailable, index_turn_for_search should return False
        without calling get_embedding."""
        from models.memory import ConversationTurn
        service._index_available = False

        turn = MagicMock(spec=ConversationTurn)
        turn.content = "some content"

        result = await service.index_turn_for_search(turn, "room-1")

        assert result is False
        service.openai_service.get_embedding.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_room_index_skips_when_unavailable(self, service):
        """When index is unavailable, delete_room_index should return False."""
        service._index_available = False

        result = await service.delete_room_index("room-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_search_reports_vector_unused_when_unavailable(self, service):
        """search() should set vector_search_used=False when index is unavailable."""
        service._index_available = False

        service._keyword_search = AsyncMock(return_value=[])
        service._merge_results = MagicMock(return_value=[])
        service._apply_mmr = MagicMock(return_value=[])

        mock_config = MagicMock()
        mock_config.enabled = True
        mock_config.vector_weight = 0.7
        mock_config.keyword_weight = 0.3
        mock_config.temporal_decay_enabled = False
        mock_config.mmr_enabled = False
        mock_config.max_results = 10
        mock_config.max_snippet_chars = 200
        mock_config.hydrate_notes = False

        with patch.object(type(service), 'config', new_callable=lambda: property(
            lambda self: mock_config
        )):
            response = await service.search("test query", "room-1")

        assert response.vector_search_used is False
        service.openai_service.get_embedding.assert_not_called()


# =========================================================================
# Test: Write-path NotFoundException handling
# =========================================================================


class TestWritePathPineconeNotFound:
    """Tests for NotFoundException handling in index_turn_for_search
    and delete_room_index."""

    @pytest.fixture
    def service(self):
        from services.memory_search_service import MemorySearchService
        svc = MemorySearchService()
        svc.openai_service = MagicMock()
        svc.openai_service.get_embedding = AsyncMock(return_value=[0.1] * 1536)
        svc._index_available = True
        return svc

    @pytest.mark.asyncio
    async def test_index_turn_returns_false_on_not_found(self, service):
        from pinecone.exceptions import NotFoundException
        from models.memory import ConversationTurn

        mock_index = MagicMock()
        mock_index.upsert.side_effect = NotFoundException("Not found")
        service.pinecone = MagicMock()
        service.pinecone.get_index = MagicMock(return_value=mock_index)

        turn = MagicMock(spec=ConversationTurn)
        turn.content = "test content"
        turn.turn_id = "turn-1"
        turn.role = MagicMock()
        turn.role.value = "user"
        turn.agent_name = "test-agent"
        turn.timestamp = MagicMock()
        turn.timestamp.isoformat.return_value = "2024-01-01T00:00:00"

        result = await service.index_turn_for_search(turn, "room-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_room_returns_false_on_not_found(self, service):
        from pinecone.exceptions import NotFoundException

        mock_index = MagicMock()
        mock_index.delete.side_effect = NotFoundException("Not found")
        service.pinecone = MagicMock()
        service.pinecone.get_index = MagicMock(return_value=mock_index)

        result = await service.delete_room_index("room-1")
        assert result is False
