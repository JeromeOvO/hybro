from __future__ import annotations

from datetime import UTC, datetime

import pytest

from context_memory import compaction
from context_memory.config import CompactionConfig
from context_memory.content_storage import ContentExpiredError, make_document_id

NOW = datetime(2026, 5, 13, tzinfo=UTC)


def now():
    return NOW


def full_turn(turn_id: str, content: str, *, tokens: int = 100):
    return {
        "turn_id": turn_id,
        "role": "user",
        "representation": "full",
        "content": content,
        "content_type": "text",
        "estimated_tokens_full": tokens,
        "estimated_tokens_compact": 20,
        "turn_notes": {"one_liner": content[:20]},
    }


def compact_turn(turn_id: str, document_id: str):
    return {
        "turn_id": turn_id,
        "role": "user",
        "representation": "compact",
        "content": None,
        "content_ref": {
            "storage_type": "mongodb",
            "collection": "conversation_content",
            "document_id": document_id,
        },
        "estimated_tokens_full": 100,
        "estimated_tokens_compact": 20,
    }


def room_doc(turns):
    return {
        "room_id": "r1",
        "memory_id": "m1",
        "memory_content": {"summary": None},
        "conversation_history": turns,
        "total_compactions": 0,
    }


class StateMemoryRepository:
    def __init__(self, doc: dict | None, *, compact_result: bool = True):
        self.doc = doc
        self.compact_result = compact_result
        self.compacted_entries: list[dict] = []
        self.get_calls = 0

    async def get_room_memory(self, room_id: str) -> dict | None:
        self.get_calls += 1
        return self.doc if self.doc and self.doc.get("room_id") == room_id else None

    async def compact_turns_bulk(
        self, room_id: str, compacted_turns: list[dict]
    ) -> bool:
        self.compacted_entries.extend(compacted_turns)
        if not self.compact_result:
            return False
        for turn in self.doc.get("conversation_history", []):
            for entry in compacted_turns:
                if turn["turn_id"] == entry["turn_id"]:
                    turn["representation"] = "compact"
                    turn["content"] = None
                    turn["content_ref"] = entry["content_ref"]
                    turn["estimated_tokens_compact"] = entry["estimated_tokens_compact"]
                    turn["brief_summary"] = entry["brief_summary"]
        self.doc["total_compactions"] = self.doc.get("total_compactions", 0) + 1
        return True


class StateContentRepository:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def upsert_full_content(self, **kwargs) -> str:
        self.docs[kwargs["document_id"]] = kwargs
        return kwargs["document_id"]

    async def get_content_by_document_id(self, document_id: str) -> dict | None:
        return self.docs.get(document_id)

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        docs = [doc for doc in self.docs.values() if doc["room_id"] == room_id]
        return {"room_id": room_id, "total_documents": len(docs)}


def config(**overrides):
    values = {
        "enabled": True,
        "max_full_turns": 2,
        "max_total_tokens": 500,
        "preserve_recent_turns": 1,
        "content_ttl_days": 0,
        "concurrency": 2,
    }
    values.update(overrides)
    return CompactionConfig(**values)


@pytest.mark.asyncio
async def test_should_compact_disabled():
    repo = StateMemoryRepository(room_doc([full_turn("t1", "hello")]))

    assert not await compaction.should_compact(repo, "r1", config(enabled=False))


@pytest.mark.asyncio
async def test_should_compact_below_threshold():
    repo = StateMemoryRepository(room_doc([full_turn("t1", "hello", tokens=10)]))

    assert not await compaction.should_compact(
        repo, "r1", config(max_full_turns=5, max_total_tokens=100)
    )


@pytest.mark.asyncio
async def test_should_compact_above_full_turns():
    repo = StateMemoryRepository(
        room_doc([full_turn("t1", "one"), full_turn("t2", "two")])
    )

    assert await compaction.should_compact(
        repo, "r1", config(max_full_turns=1, max_total_tokens=999)
    )


@pytest.mark.asyncio
async def test_should_compact_uses_unwindowed_canonical_history():
    direct_turns = [
        full_turn(f"t{index}", f"short {index}", tokens=1) for index in range(1, 22)
    ]
    doc = room_doc(direct_turns)
    repo = StateMemoryRepository(doc)
    default_config = CompactionConfig(
        enabled=True,
        max_total_tokens=80_000,
        preserve_recent_turns=10,
        content_ttl_days=0,
        concurrency=2,
    )

    assert default_config.max_full_turns == 20
    assert await compaction.should_compact(repo, "r1", default_config)


@pytest.mark.asyncio
async def test_should_compact_above_token_threshold():
    repo = StateMemoryRepository(room_doc([full_turn("t1", "one", tokens=50)]))

    assert await compaction.should_compact(
        repo, "r1", config(max_full_turns=5, max_total_tokens=10)
    )


@pytest.mark.asyncio
async def test_compact_room_memory_preserves_recent():
    repo = StateMemoryRepository(
        room_doc(
            [full_turn("t1", "one"), full_turn("t2", "two"), full_turn("t3", "three")]
        )
    )

    result = await compaction.compact_room_memory(
        repository=repo,
        content_repository=StateContentRepository(),
        room_id="r1",
        room_memory_doc=None,
        config=config(preserve_recent_turns=1),
        now=now,
    )

    assert result.compacted_count == 2
    assert [entry["turn_id"] for entry in repo.compacted_entries] == ["t1", "t2"]
    assert repo.doc["conversation_history"][2]["representation"] == "full"


@pytest.mark.asyncio
async def test_compact_room_memory_stores_content_and_bounded_brief_summary():
    semantic_content = "  Deploy   the React frontend with zero downtime.  " + (
        "retain details " * 30
    )
    repo = StateMemoryRepository(
        room_doc([full_turn("t1", semantic_content), full_turn("t2", "two")])
    )
    content_repo = StateContentRepository()

    await compaction.compact_room_memory(
        repository=repo,
        content_repository=content_repo,
        room_id="r1",
        room_memory_doc=None,
        config=config(preserve_recent_turns=0),
        now=now,
    )

    assert make_document_id("r1", "t1") in content_repo.docs
    assert (
        content_repo.docs[make_document_id("r1", "t1")]["content"] == semantic_content
    )
    summary = repo.doc["conversation_history"][0]["brief_summary"]
    assert summary.startswith("Deploy the React frontend with zero downtime.")
    assert "  " not in summary
    assert len(summary) == compaction.BRIEF_SUMMARY_MAX_CHARS
    assert summary.endswith("...")


@pytest.mark.asyncio
async def test_compact_room_memory_stale_snapshot_already_compact_is_clean_noop():
    stale_snapshot = room_doc([full_turn("t1", "one")])
    live_doc = room_doc([compact_turn("t1", "doc-existing")])
    repo = StateMemoryRepository(live_doc, compact_result=False)

    result = await compaction.compact_room_memory(
        repository=repo,
        content_repository=StateContentRepository(),
        room_id="r1",
        room_memory_doc=stale_snapshot,
        config=config(preserve_recent_turns=0),
        now=now,
    )

    assert result.compacted_count == 0
    assert result.tokens_saved == 0
    assert result.metadata["errors"] == []
    assert repo.compacted_entries[0]["turn_id"] == "t1"


@pytest.mark.asyncio
async def test_compact_if_needed_returns_none_below_threshold():
    repo = StateMemoryRepository(room_doc([full_turn("t1", "one", tokens=5)]))

    assert (
        await compaction.compact_if_needed(
            repository=repo,
            content_repository=StateContentRepository(),
            room_id="r1",
            config=config(max_full_turns=5, max_total_tokens=999),
            now=now,
        )
        is None
    )


@pytest.mark.asyncio
async def test_compact_if_needed_uses_single_memory_load_when_compacting():
    repo = StateMemoryRepository(
        room_doc([full_turn("t1", "one"), full_turn("t2", "two")])
    )

    result = await compaction.compact_if_needed(
        repository=repo,
        content_repository=StateContentRepository(),
        room_id="r1",
        config=config(max_full_turns=1, preserve_recent_turns=0),
        now=now,
    )

    assert result.compacted_count == 2
    assert repo.get_calls == 1


@pytest.mark.asyncio
async def test_run_compaction_uses_single_memory_load_when_compacting():
    repo = StateMemoryRepository(
        room_doc([full_turn("t1", "one"), full_turn("t2", "two")])
    )

    result = await compaction.run_compaction(
        repository=repo,
        content_repository=StateContentRepository(),
        room_id="r1",
        config=config(max_full_turns=1, preserve_recent_turns=0),
        now=now,
    )

    assert result.compacted_count == 2
    assert repo.get_calls == 1


@pytest.mark.asyncio
async def test_run_compaction_returns_skipped_result():
    repo = StateMemoryRepository(room_doc([full_turn("t1", "one", tokens=5)]))

    result = await compaction.run_compaction(
        repository=repo,
        content_repository=StateContentRepository(),
        room_id="r1",
        config=config(max_full_turns=5, max_total_tokens=999),
        now=now,
    )

    assert result.compacted_count == 0
    assert result.metadata == {"skipped": True, "reason": "below_threshold"}


@pytest.mark.asyncio
async def test_expand_turn_content_full_turn():
    assert (
        await compaction.expand_turn_content_from_turn(
            StateContentRepository(),
            full_turn("t1", "full content"),
        )
        == "full content"
    )


@pytest.mark.asyncio
async def test_expand_turn_content_compact_turn():
    content_repo = StateContentRepository()
    await content_repo.upsert_full_content(
        document_id="doc1",
        room_id="r1",
        turn_id="t1",
        content="stored",
        content_type="text",
        content_hash="hash",
        stored_at=NOW,
    )

    assert (
        await compaction.expand_turn_content_from_turn(
            content_repo,
            compact_turn("t1", "doc1"),
        )
        == "stored"
    )


@pytest.mark.asyncio
async def test_expand_turn_content_compact_turn_rejects_expired_document():
    content_repo = StateContentRepository()
    await content_repo.upsert_full_content(
        document_id="doc1",
        room_id="r1",
        turn_id="t1",
        content="expired",
        content_type="text",
        content_hash="hash",
        stored_at=NOW,
        expires_at=datetime(2026, 5, 12, tzinfo=UTC),
    )

    with pytest.raises(ContentExpiredError):
        await compaction.expand_turn_content_from_turn(
            content_repo,
            compact_turn("t1", "doc1"),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_expand_turn_content_from_turn_missing_ref():
    with pytest.raises(ValueError, match="missing content reference"):
        await compaction.expand_turn_content_from_turn(
            StateContentRepository(),
            {"turn_id": "t1", "role": "user", "representation": "compact"},
        )


@pytest.mark.asyncio
async def test_fetch_turn_content_missing_room():
    result = await compaction.fetch_turn_content(
        StateMemoryRepository(None),
        StateContentRepository(),
        turn_id="t1",
        room_id="missing",
    )

    assert result == "[Error: Room missing not found]"


@pytest.mark.asyncio
async def test_get_compaction_stats():
    content_repo = StateContentRepository()
    await content_repo.upsert_full_content(
        document_id="doc1",
        room_id="r1",
        turn_id="t1",
        content="stored",
        content_type="text",
        content_hash="hash",
        stored_at=NOW,
    )
    repo = StateMemoryRepository(
        room_doc([full_turn("t1", "one", tokens=50), compact_turn("t2", "doc1")])
    )

    stats = await compaction.get_compaction_stats(repo, content_repo, "r1")

    assert stats["total_turns"] == 2
    assert stats["full_turns"] == 1
    assert stats["compact_turns"] == 1
    assert stats["content_storage"]["total_documents"] == 1
