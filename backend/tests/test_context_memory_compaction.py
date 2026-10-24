from __future__ import annotations

from datetime import UTC, datetime

import pytest

from common.utils.context_utils import estimate_tokens
from context_memory import compaction
from context_memory.config import CompactionConfig
from context_memory.content_storage import make_document_id
from context_memory.translators import normalize_room_memory

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

    async def get_room_memory(self, conversation_id: str) -> dict | None:
        self.get_calls += 1
        return (
            self.doc
            if self.doc and self.doc.get("room_id") == conversation_id
            else None
        )

    async def compact_turns_bulk(
        self, conversation_id: str, compacted_turns: list[dict]
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

    assert not compaction._doc_should_compact(
        await repo.get_room_memory("r1"), config(enabled=False)
    )


@pytest.mark.asyncio
async def test_should_compact_below_threshold():
    repo = StateMemoryRepository(room_doc([full_turn("t1", "hello", tokens=10)]))

    assert not compaction._doc_should_compact(
        await repo.get_room_memory("r1"), config(max_full_turns=5, max_total_tokens=100)
    )


@pytest.mark.asyncio
async def test_should_compact_above_full_turns():
    repo = StateMemoryRepository(
        room_doc([full_turn("t1", "one"), full_turn("t2", "two")])
    )

    assert compaction._doc_should_compact(
        await repo.get_room_memory("r1"), config(max_full_turns=1, max_total_tokens=999)
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
    assert compaction._doc_should_compact(
        await repo.get_room_memory("r1"), default_config
    )


@pytest.mark.asyncio
async def test_should_compact_above_token_threshold():
    repo = StateMemoryRepository(room_doc([full_turn("t1", "one", tokens=50)]))

    assert compaction._doc_should_compact(
        await repo.get_room_memory("r1"), config(max_full_turns=5, max_total_tokens=10)
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
        conversation_id="r1",
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

    result = await compaction.compact_room_memory(
        repository=repo,
        content_repository=content_repo,
        conversation_id="r1",
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
    compacted_turns = normalize_room_memory(repo.doc).conversation_history
    compact_estimate = compacted_turns[0].estimated_tokens_compact
    assert compact_estimate == estimate_tokens(compacted_turns[0].to_context_string())
    assert compact_estimate > 20
    assert result.tokens_saved == sum(
        max(0, turn.estimated_tokens_full - turn.estimated_tokens_compact)
        for turn in compacted_turns
    )


@pytest.mark.asyncio
async def test_compact_room_memory_stale_snapshot_already_compact_is_clean_noop():
    stale_snapshot = room_doc([full_turn("t1", "one")])
    live_doc = room_doc([compact_turn("t1", "doc-existing")])
    repo = StateMemoryRepository(live_doc, compact_result=False)

    result = await compaction.compact_room_memory(
        repository=repo,
        content_repository=StateContentRepository(),
        conversation_id="r1",
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
            conversation_id="r1",
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
        conversation_id="r1",
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
        conversation_id="r1",
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
        conversation_id="r1",
        config=config(max_full_turns=5, max_total_tokens=999),
        now=now,
    )

    assert result.compacted_count == 0
    assert result.metadata == {"skipped": True, "reason": "below_threshold"}
