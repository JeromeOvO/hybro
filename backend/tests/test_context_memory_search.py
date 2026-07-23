from datetime import UTC, datetime, timedelta

import pytest

from context_memory.config import MemorySearchConfig
from context_memory.models import SearchRankingRecord
from context_memory.search import rank_keyword_results, search_memory


class ContentRepository:
    def __init__(self, rows: list[dict], hydrated: dict[str, dict]):
        self.rows = rows
        self.hydrated = hydrated
        self.search_calls: list[tuple[int, int]] = []
        self.search_exclusions: list[set[str]] = []
        self.hydration_calls: list[list[str]] = []

    async def scan_text_search(self, room_id, query):
        del room_id, query
        self.search_calls.append((0, 0))
        return list(self.rows)

    async def text_search(
        self,
        room_id,
        query,
        limit=50,
        skip=0,
    ):
        del room_id, query
        self.search_calls.append((skip, limit))
        return self.rows[skip : skip + limit]

    async def hydrate_turn_content(self, room_id, turn_ids):
        del room_id
        self.hydration_calls.append(list(turn_ids))
        return [
            self.hydrated[turn_id] for turn_id in turn_ids if turn_id in self.hydrated
        ]


def _row(turn_id: str, score: float, timestamp: datetime) -> dict:
    return {
        "turn_id": turn_id,
        "score": score,
        "turn_timestamp": timestamp,
        "stored_at": timestamp + timedelta(hours=1),
    }


def _content(turn_id: str, content: str, *, one_liner: str | None = None) -> dict:
    return {
        "turn_id": turn_id,
        "content": content,
        "content_type": "text",
        "turn_notes": {"one_liner": one_liner} if one_liner else {},
    }


@pytest.mark.asyncio
async def test_keyword_search_hydrates_content_and_prefers_one_liner():
    now = datetime.now(UTC)
    repository = ContentRepository(
        [_row("t1", 4.0, now)],
        {"t1": _content("t1", "full content", one_liner="short summary")},
    )
    results, response = await search_memory(
        room_id="r1",
        query="summary",
        limit=5,
        content_repository=repository,
        config=MemorySearchConfig(max_snippet_chars=300),
    )
    assert results[0].content == "short summary"
    assert results[0].keyword_score == 1.0
    assert results[0].relevance_score <= 1.0
    assert results[0].temporal_decay_factor <= 1.0
    assert "score" not in results[0].model_dump()
    assert response["keyword_search_used"] is True
    assert "vector_search_used" not in response
    assert "mmr_applied" not in response


@pytest.mark.asyncio
async def test_missing_hydration_is_dropped_and_later_pages_backfill():
    now = datetime.now(UTC)
    rows = [_row(f"missing-{index}", 100 - index, now) for index in range(50)]
    rows.append(_row("valid", 1.0, now))
    repository = ContentRepository(
        rows,
        {"valid": _content("valid", "backfilled content")},
    )
    results, _ = await search_memory(
        room_id="r1",
        query="content",
        limit=1,
        content_repository=repository,
        config=MemorySearchConfig(),
    )
    assert [result.metadata["turn_id"] for result in results] == ["valid"]
    assert repository.search_calls == [(0, 0)]
    hydrated_ids = [
        turn_id
        for hydration_call in repository.hydration_calls
        for turn_id in hydration_call
    ]
    assert len(hydrated_ids) == len(set(hydrated_ids))


@pytest.mark.asyncio
async def test_empty_hydrated_content_does_not_prevent_later_backfill():
    now = datetime.now(UTC)
    rows = [_row(f"empty-{index}", 100 - index, now) for index in range(50)]
    rows.append(_row("valid", 1.0, now))
    repository = ContentRepository(
        rows,
        {
            **{f"empty-{index}": _content(f"empty-{index}", "") for index in range(50)},
            "valid": _content("valid", "backfilled content"),
        },
    )

    results, _ = await search_memory(
        room_id="r1",
        query="content",
        limit=1,
        content_repository=repository,
        config=MemorySearchConfig(),
    )

    assert [result.metadata["turn_id"] for result in results] == ["valid"]


@pytest.mark.asyncio
async def test_ttl_deletion_during_hydration_does_not_shift_away_backfill():
    now = datetime.now(UTC)

    class TtlRaceRepository(ContentRepository):
        async def hydrate_turn_content(self, room_id, turn_ids):
            del room_id
            if not self.hydration_calls:
                self.hydration_calls.append(list(turn_ids))
                self.rows = self.rows[50:]
                return []
            self.hydration_calls.append(list(turn_ids))
            return [
                self.hydrated[turn_id]
                for turn_id in turn_ids
                if turn_id in self.hydrated
            ]

    rows = [_row(f"expired-{index}", 100 - index, now) for index in range(50)]
    rows.append(_row("valid", 1.0, now))
    repository = TtlRaceRepository(
        rows,
        {"valid": _content("valid", "surviving content")},
    )

    results, _ = await search_memory(
        room_id="r1",
        query="content",
        limit=1,
        content_repository=repository,
        config=MemorySearchConfig(),
    )

    assert [result.metadata["turn_id"] for result in results] == ["valid"]
    assert repository.search_calls == [(0, 0)]


@pytest.mark.asyncio
async def test_temporal_ranking_scans_all_keyword_candidates_before_top_k():
    now = datetime.now(UTC)
    old = now - timedelta(days=365)
    rows = [_row(f"old-{index}", 100 - index, old) for index in range(50)]
    rows.append(_row("recent", 1.0, now))
    repository = ContentRepository(
        rows,
        {
            **{f"old-{index}": _content(f"old-{index}", "old") for index in range(50)},
            "recent": _content("recent", "recent"),
        },
    )

    results, _ = await search_memory(
        room_id="r1",
        query="content",
        limit=1,
        content_repository=repository,
        config=MemorySearchConfig(half_life_days=30),
    )

    assert [result.metadata["turn_id"] for result in results] == ["recent"]
    assert repository.search_calls == [(0, 0)]


@pytest.mark.asyncio
async def test_content_prefix_is_used_without_placeholder_text():
    now = datetime.now(UTC)
    repository = ContentRepository(
        [_row("t1", 1.0, now)],
        {"t1": _content("t1", "x" * 500)},
    )
    results, _ = await search_memory(
        room_id="r1",
        query="x",
        limit=1,
        content_repository=repository,
        config=MemorySearchConfig(max_snippet_chars=300),
    )
    assert results[0].content == "x" * 300
    assert results[0].content != "[text]"


def test_temporal_decay_clamps_future_timestamps_and_uses_stable_ties():
    now = datetime.now(UTC)
    records = [
        SearchRankingRecord(
            "b", "r1", keyword_score=1, timestamp=now + timedelta(days=2)
        ),
        SearchRankingRecord(
            "a", "r1", keyword_score=1, timestamp=now + timedelta(days=2)
        ),
        SearchRankingRecord(
            "old", "r1", keyword_score=1, timestamp=now - timedelta(days=30)
        ),
    ]
    ranked = rank_keyword_results(
        records,
        temporal_decay_enabled=True,
        half_life_days=30,
    )
    assert [record.turn_id for record in ranked[:2]] == ["a", "b"]
    assert ranked[0].temporal_decay_factor == 1.0
    assert ranked[-1].relevance_score < ranked[0].relevance_score


def test_repeated_ranking_preserves_raw_keyword_scores():
    now = datetime.now(UTC)
    records = [
        SearchRankingRecord("first", "r1", keyword_score=10, timestamp=now),
    ]
    rank_keyword_results(
        records,
        temporal_decay_enabled=False,
        half_life_days=30,
    )
    records.append(SearchRankingRecord("second", "r1", keyword_score=5, timestamp=now))

    ranked = rank_keyword_results(
        records,
        temporal_decay_enabled=False,
        half_life_days=30,
    )

    assert [record.turn_id for record in ranked] == ["first", "second"]
    assert [record.raw_keyword_score for record in ranked] == [10, 5]
    assert [record.keyword_score for record in ranked] == [1.0, 0.5]


def test_memory_search_config_defaults_to_300_character_snippets():
    from common.config.settings import Settings

    assert Settings.model_fields["memory_search_max_snippet_chars"].default == 300


@pytest.mark.asyncio
async def test_keyword_failure_returns_empty_relevant_memory():
    class BrokenRepository(ContentRepository):
        async def scan_text_search(self, room_id, query):
            raise RuntimeError("text index missing")

    results, response = await search_memory(
        room_id="r1",
        query="anything",
        limit=5,
        content_repository=BrokenRepository([], {}),
        config=MemorySearchConfig(),
    )
    assert results == []
    assert response["keyword_search_used"] is False
