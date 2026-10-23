"""Owner-level tests for ContextMemory compaction runtime behavior.

These tests intentionally exercise context_memory.compaction and
ContextMemoryFacade directly instead of the legacy application shell shim.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from context_memory import ContextMemoryFacade, compaction
from context_memory.config import CompactionConfig
from context_memory.content_storage import hash_content, make_document_id

NOW = datetime(2026, 5, 13, tzinfo=UTC)


def now() -> datetime:
    return NOW


def full_turn(turn_id: str, content: str, *, tokens: int = 100) -> dict:
    return {
        "turn_id": turn_id,
        "role": "user",
        "content": content,
        "content_type": "text",
        "representation": "full",
        "estimated_tokens_full": tokens,
        "estimated_tokens_compact": 20,
        "timestamp": NOW,
    }


def room_doc(turns: list[dict]) -> dict:
    return {
        "room_id": "r1",
        "memory_id": "m1",
        "memory_content": {"summary": None},
        "conversation_history": turns,
        "total_compactions": 0,
    }


class MemoryRepositorySpy:
    def __init__(self, doc: dict | None):
        self.doc = doc
        self.compacted_entries: list[dict] = []
        self.get_calls = 0

    async def get_room_memory(self, conversation_id: str) -> dict | None:
        self.get_calls += 1
        if self.doc and self.doc.get("room_id") == conversation_id:
            return self.doc
        return None

    async def create_room_memory(self, memory: dict) -> str:
        self.doc = memory
        return memory["memory_id"]

    async def delete_room_memory(self, conversation_id: str) -> bool:
        if self.doc and self.doc.get("room_id") == conversation_id:
            self.doc = None
            return True
        return False

    async def compact_turns_bulk(
        self, conversation_id: str, compacted_turns: list[dict]
    ) -> bool:
        self.compacted_entries.extend(compacted_turns)
        if not self.doc or self.doc.get("room_id") != conversation_id:
            return False
        by_id = {entry["turn_id"]: entry for entry in compacted_turns}
        for turn in self.doc["conversation_history"]:
            entry = by_id.get(turn["turn_id"])
            if entry is None:
                continue
            turn["representation"] = "compact"
            turn["content"] = None
            turn["content_ref"] = entry["content_ref"]
            turn["estimated_tokens_compact"] = entry["estimated_tokens_compact"]
        self.doc["total_compactions"] = self.doc.get("total_compactions", 0) + 1
        return True


class ContentRepositorySpy:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def upsert_full_content(self, **kwargs) -> str:
        self.docs[kwargs["document_id"]] = kwargs
        return kwargs["document_id"]

    async def get_content_by_turn_id(
        self, conversation_id: str, turn_id: str
    ) -> dict | None:
        for doc in self.docs.values():
            if doc["room_id"] == conversation_id and doc["turn_id"] == turn_id:
                return doc
        return None

    async def delete_content_by_room_id(self, _conversation_id: str) -> int:
        return 0


class RoomHistoryReaderStub:
    async def get_messages_for_room(
        self, _conversation_id: str, _limit: int, before=None
    ):
        return []

    async def get_messages_by_ids(self, _message_ids: list[str]):
        return []

    async def get_message_thread(self, _parent_message_id: str):
        return []


def config(**overrides) -> CompactionConfig:
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


def facade(
    *,
    memory_repository: MemoryRepositorySpy,
    content_repository: ContentRepositorySpy,
    compaction_config: CompactionConfig | None = None,
) -> ContextMemoryFacade:
    return ContextMemoryFacade(
        memory_repository=memory_repository,
        content_repository=content_repository,
        room_history_reader=RoomHistoryReaderStub(),
        id_factory=lambda: "id-1",
        now=now,
        compaction_config=compaction_config or config(),
    )


def test_hash_content_is_deterministic_sha256_hex() -> None:
    result = hash_content("test content")

    assert result == hash_content("test content")
    assert result != hash_content("other content")
    assert len(result) == 64


@pytest.mark.asyncio
async def test_should_compact_uses_context_memory_thresholds() -> None:
    repo = MemoryRepositorySpy(
        room_doc([full_turn("t1", "one"), full_turn("t2", "two")])
    )

    doc = await repo.get_room_memory("r1")

    assert compaction._doc_should_compact(
        doc, config(max_full_turns=1, max_total_tokens=999)
    )
    assert not compaction._doc_should_compact(
        await repo.get_room_memory("missing"), config()
    )
    assert not compaction._doc_should_compact(doc, config(enabled=False))


@pytest.mark.asyncio
async def test_compact_room_memory_preserves_recent_and_stores_content_hash() -> None:
    repo = MemoryRepositorySpy(
        room_doc(
            [
                full_turn("t1", "old one", tokens=80),
                full_turn("t2", "old two", tokens=90),
                full_turn("t3", "recent", tokens=100),
            ]
        )
    )
    content_repo = ContentRepositorySpy()

    result = await compaction.compact_room_memory(
        repository=repo,
        content_repository=content_repo,
        conversation_id="r1",
        room_memory_doc=None,
        config=config(preserve_recent_turns=1),
        now=now,
    )

    assert result.compacted_count == 2
    assert result.tokens_saved == sum(
        max(0, full_tokens - entry["estimated_tokens_compact"])
        for full_tokens, entry in zip((80, 90), repo.compacted_entries, strict=True)
    )
    assert [entry["turn_id"] for entry in repo.compacted_entries] == ["t1", "t2"]
    assert repo.doc["conversation_history"][2]["representation"] == "full"
    stored = content_repo.docs[make_document_id("r1", "t1")]
    assert stored["content"] == "old one"
    assert stored["content_hash"] == hash_content("old one")


@pytest.mark.asyncio
async def test_facade_compact_if_needed_persists_searchable_content() -> None:
    repo = MemoryRepositorySpy(room_doc([full_turn("t1", "index this turn")]))
    content_repo = ContentRepositorySpy()
    service = facade(
        memory_repository=repo,
        content_repository=content_repo,
        compaction_config=config(max_full_turns=0, preserve_recent_turns=0),
    )

    result = await service.compact_if_needed("r1")

    assert result is not None
    assert result.compacted_count == 1
    assert repo.compacted_entries[0]["turn_id"] == "t1"
    assert content_repo.docs[make_document_id("r1", "t1")]["content"] == (
        "index this turn"
    )
    assert content_repo.docs[make_document_id("r1", "t1")]["turn_timestamp"] == NOW


@pytest.mark.asyncio
async def test_facade_compact_if_needed_returns_none_below_threshold() -> None:
    service = facade(
        memory_repository=MemoryRepositorySpy(
            room_doc([full_turn("t1", "small", tokens=5)])
        ),
        content_repository=ContentRepositorySpy(),
        compaction_config=config(max_full_turns=5, max_total_tokens=999),
    )

    assert await service.compact_if_needed("r1") is None
