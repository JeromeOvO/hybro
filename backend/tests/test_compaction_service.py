"""Owner-level tests for ContextMemory compaction runtime behavior.

These tests intentionally exercise context_memory.compaction and
ContextMemoryFacade directly instead of the legacy application shell shim.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from context_memory import ContextMemoryFacade, compaction
from context_memory.config import CompactionConfig, MemorySearchConfig
from context_memory.content_storage import (
    ContentExpiredError,
    hash_content,
    make_document_id,
)

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
        "turn_notes": {"one_liner": content[:40]},
        "timestamp": NOW,
    }


def compact_turn(turn_id: str, document_id: str) -> dict:
    return {
        "turn_id": turn_id,
        "role": "user",
        "content": None,
        "content_type": "text",
        "representation": "compact",
        "content_ref": {
            "storage_type": "mongodb",
            "collection": "conversation_content",
            "document_id": document_id,
            "created_at": NOW,
        },
        "estimated_tokens_full": 100,
        "estimated_tokens_compact": 20,
        "timestamp": NOW,
    }


def room_doc(turns: list[dict]) -> dict:
    return {
        "room_id": "r1",
        "memory_id": "m1",
        "memory_content": {"conversation_history": turns},
        "conversation_history": turns,
        "total_compactions": 0,
    }


class MemoryRepositorySpy:
    def __init__(self, doc: dict | None):
        self.doc = doc
        self.compacted_entries: list[dict] = []
        self.get_calls = 0

    async def get_room_memory(self, room_id: str) -> dict | None:
        self.get_calls += 1
        if self.doc and self.doc.get("room_id") == room_id:
            return self.doc
        return None

    async def get_user_memories(self, _user_id: str) -> list[dict]:
        return []

    async def create_room_memory(self, memory: dict) -> str:
        self.doc = memory
        return memory["memory_id"]

    async def delete_room_memory(self, room_id: str) -> bool:
        if self.doc and self.doc.get("room_id") == room_id:
            self.doc = None
            return True
        return False

    async def compact_turns_bulk(
        self, room_id: str, compacted_turns: list[dict]
    ) -> bool:
        self.compacted_entries.extend(compacted_turns)
        if not self.doc or self.doc.get("room_id") != room_id:
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
        self.doc["memory_content"]["conversation_history"] = self.doc[
            "conversation_history"
        ]
        self.doc["total_compactions"] = self.doc.get("total_compactions", 0) + 1
        return True


class ContentRepositorySpy:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def upsert_full_content(self, **kwargs) -> str:
        self.docs[kwargs["document_id"]] = kwargs
        return kwargs["document_id"]

    async def get_content_by_document_id(self, document_id: str) -> dict | None:
        return self.docs.get(document_id)

    async def get_content_by_turn_id(self, room_id: str, turn_id: str) -> dict | None:
        for doc in self.docs.values():
            if doc["room_id"] == room_id and doc["turn_id"] == turn_id:
                return doc
        return None

    async def delete_content_by_room_id(self, _room_id: str) -> int:
        return 0

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        docs = [doc for doc in self.docs.values() if doc["room_id"] == room_id]
        return {"room_id": room_id, "total_documents": len(docs)}

    async def text_search(self, _room_id: str, _query: str, limit: int = 50):
        return []


class RoomHistoryReaderStub:
    async def get_messages_for_room(self, _room_id: str, _limit: int, before=None):
        return []

    async def get_messages_by_ids(self, _message_ids: list[str]):
        return []

    async def get_message_thread(self, _parent_message_id: str):
        return []


class VectorSpy:
    def __init__(self):
        self.upserted: list[tuple[str, list]] = []
        self.deleted: list[tuple[str, dict]] = []

    async def search(self, _index, _vector, _top_k, filter=None):
        return []

    async def upsert(self, index, records):
        self.upserted.append((index, records))

    async def delete_by_filter(self, index, filter):
        self.deleted.append((index, filter))


class LLMStub:
    async def generate(self, _request):
        return None

    async def generate_structured(self, _messages, _schema, model=None):
        return type("Response", (), {"data": {}})()

    async def embed(self, text: str):
        return [float(len(text)), 1.0]

    async def embed_batch(self, texts: list[str]):
        return [[float(len(text)), 1.0] for text in texts]


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
    vector: VectorSpy | None = None,
    compaction_config: CompactionConfig | None = None,
) -> ContextMemoryFacade:
    return ContextMemoryFacade(
        memory_repository=memory_repository,
        content_repository=content_repository,
        room_history_reader=RoomHistoryReaderStub(),
        vector=vector or VectorSpy(),
        llm_provider=LLMStub(),
        id_factory=lambda: "id-1",
        now=now,
        compaction_config=compaction_config or config(),
        search_config=MemorySearchConfig(enabled=True, index_name="room-memory-test"),
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

    assert await compaction.should_compact(
        repo, "r1", config(max_full_turns=1, max_total_tokens=999)
    )
    assert not await compaction.should_compact(repo, "missing", config())
    assert not await compaction.should_compact(repo, "r1", config(enabled=False))


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
        room_id="r1",
        room_memory_doc=None,
        config=config(preserve_recent_turns=1),
        now=now,
    )

    assert result.compacted_count == 2
    assert result.tokens_saved == 130
    assert [entry["turn_id"] for entry in repo.compacted_entries] == ["t1", "t2"]
    assert repo.doc["conversation_history"][2]["representation"] == "full"
    stored = content_repo.docs[make_document_id("r1", "t1")]
    assert stored["content"] == "old one"
    assert stored["content_hash"] == hash_content("old one")


@pytest.mark.asyncio
async def test_facade_compact_if_needed_indexes_turns_through_context_memory() -> None:
    repo = MemoryRepositorySpy(room_doc([full_turn("t1", "index this turn")]))
    content_repo = ContentRepositorySpy()
    vector = VectorSpy()
    service = facade(
        memory_repository=repo,
        content_repository=content_repo,
        vector=vector,
        compaction_config=config(max_full_turns=0, preserve_recent_turns=0),
    )

    result = await service.compact_if_needed("r1")

    assert result is not None
    assert result.compacted_count == 1
    assert repo.compacted_entries[0]["turn_id"] == "t1"
    assert content_repo.docs[make_document_id("r1", "t1")]["content"] == (
        "index this turn"
    )
    assert vector.upserted
    index_name, records = vector.upserted[0]
    assert index_name == "room-memory-test"
    assert records[0].id == "t1"
    assert records[0].metadata["room_id"] == "r1"


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


@pytest.mark.asyncio
async def test_expand_turn_content_returns_full_and_mongodb_content() -> None:
    content_repo = ContentRepositorySpy()
    await content_repo.upsert_full_content(
        document_id="doc1",
        room_id="r1",
        turn_id="t1",
        content="stored content",
        content_type="text",
        content_hash="hash",
        stored_at=NOW,
    )

    assert (
        await compaction.expand_turn_content_from_turn(
            content_repo, full_turn("t-full", "inline content")
        )
        == "inline content"
    )
    assert (
        await compaction.expand_turn_content_from_turn(
            content_repo, compact_turn("t1", "doc1"), now=NOW
        )
        == "stored content"
    )


@pytest.mark.asyncio
async def test_expand_turn_content_raises_when_stored_content_expired() -> None:
    content_repo = ContentRepositorySpy()
    await content_repo.upsert_full_content(
        document_id="doc1",
        room_id="r1",
        turn_id="t1",
        content="expired content",
        content_type="text",
        content_hash="hash",
        stored_at=datetime(2026, 5, 1, tzinfo=UTC),
        expires_at=datetime(2026, 5, 12, tzinfo=UTC),
    )

    with pytest.raises(ContentExpiredError):
        await compaction.expand_turn_content_from_turn(
            content_repo, compact_turn("t1", "doc1"), now=NOW
        )


@pytest.mark.asyncio
async def test_fetch_turn_content_returns_graceful_fallback_errors() -> None:
    missing_room = await compaction.fetch_turn_content(
        MemoryRepositorySpy(None),
        ContentRepositorySpy(),
        turn_id="t1",
        room_id="missing",
    )
    missing_turn = await compaction.fetch_turn_content(
        MemoryRepositorySpy(room_doc([full_turn("t1", "one")])),
        ContentRepositorySpy(),
        turn_id="t2",
        room_id="r1",
    )
    unsupported = await compaction.fetch_turn_content(
        MemoryRepositorySpy(
            room_doc(
                [
                    {
                        **compact_turn("t3", "doc3"),
                        "content_ref": {"storage_type": "url", "url": "https://x.test"},
                    }
                ]
            )
        ),
        ContentRepositorySpy(),
        turn_id="t3",
        room_id="r1",
    )

    assert missing_room == "[Error: Room missing not found]"
    assert missing_turn == "[Error: Turn t2 not found in room history]"
    assert "unsupported storage" in unsupported


@pytest.mark.asyncio
async def test_compaction_continues_when_vector_indexing_fails() -> None:
    repo = MemoryRepositorySpy(room_doc([full_turn("t1", "content")]))

    async def fail_index(_room_id: str, _turn_doc: dict) -> bool:
        return False

    result = await compaction.compact_room_memory(
        repository=repo,
        content_repository=ContentRepositorySpy(),
        room_id="r1",
        room_memory_doc=None,
        config=config(preserve_recent_turns=0),
        now=now,
        index_turn=fail_index,
    )

    assert result.compacted_count == 1
    assert result.metadata["errors"] == ["Failed to index turn t1"]
