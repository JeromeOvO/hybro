from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from context_memory import compaction
from context_memory.config import CompactionConfig
from context_memory.repository import (
    ContentStorageMongoRepository,
    MemoryMongoRepository,
)
from context_memory.translators import normalize_room_memory


class FakeMongoCollection:
    def __init__(self):
        self.documents: list[dict] = []
        self.find_calls: list[tuple[dict, dict]] = []
        self.update_one_calls: list[tuple[dict, dict | list[dict], dict]] = []
        self.raise_on_update_one = False
        self.update_one_exception: Exception | None = None

    async def find_one(self, query: dict, **kwargs):
        for doc in self.documents:
            if _matches(doc, query):
                return deepcopy(doc)
        return None

    async def find(self, query: dict, **kwargs):
        self.find_calls.append((deepcopy(query), deepcopy(kwargs)))
        limit = kwargs.get("limit")
        docs = [deepcopy(doc) for doc in self.documents if _matches(doc, query)]
        return docs[:limit] if limit is not None else docs

    async def find_one_and_update(
        self, query: dict, update: dict | list[dict], **kwargs
    ):
        for doc in self.documents:
            if _matches(doc, query):
                _apply_update(doc, update)
                return deepcopy(doc) if kwargs.get("return_document") else None
        if kwargs.get("upsert"):
            doc = {}
            if "$setOnInsert" in update:
                doc.update(deepcopy(update["$setOnInsert"]))
            else:
                doc.update({k: v for k, v in query.items() if not isinstance(v, dict)})
            if isinstance(update, dict):
                _apply_update(doc, update, is_insert=True)
            self.documents.append(doc)
            return deepcopy(doc) if kwargs.get("return_document") else None
        return None

    async def insert_one(self, document: dict) -> str:
        doc = deepcopy(document)
        doc.setdefault("_id", f"id-{len(self.documents) + 1}")
        self.documents.append(doc)
        return str(doc["_id"])

    async def update_one(
        self, query: dict, update: dict | list[dict], **kwargs
    ) -> bool:
        self.update_one_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        if self.raise_on_update_one:
            raise RuntimeError("write failed")
        if self.update_one_exception is not None:
            raise self.update_one_exception
        if kwargs.get("upsert"):
            _assert_no_update_path_conflicts(update)
        for doc in self.documents:
            if _matches(doc, query):
                before = deepcopy(doc)
                _apply_update(doc, update, array_filters=kwargs.get("array_filters"))
                return doc != before
        if kwargs.get("upsert"):
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            _apply_update(doc, update, is_insert=True)
            self.documents.append(doc)
            return True
        return False

    async def delete_one(self, query: dict) -> bool:
        for index, doc in enumerate(self.documents):
            if _matches(doc, query):
                self.documents.pop(index)
                return True
        return False

    async def delete_many(self, query: dict) -> int:
        before = len(self.documents)
        self.documents = [doc for doc in self.documents if not _matches(doc, query)]
        return before - len(self.documents)

    async def aggregate(self, pipeline: list[dict]) -> list[dict]:
        docs = [deepcopy(doc) for doc in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [doc for doc in docs if _matches(doc, stage["$match"])]
            elif "$group" in stage:
                grouped = {}
                group_id = stage["$group"]["_id"].lstrip("$")
                for doc in docs:
                    key = _get_path(doc, group_id)
                    row = grouped.setdefault(
                        key, {"_id": key, "count": 0, "total_size": 0}
                    )
                    row["count"] += 1
                    row["total_size"] += len(doc.get("content", "").encode())
                docs = list(grouped.values())
        return docs

    async def find_one_by_stable_or_native_id(self, field: str, value: str):
        return await self.find_one({field: value}) or await self.find_one(
            {"_id": value}
        )


class FakeMongo:
    def __init__(self):
        self.collections = {
            "room_memories": FakeMongoCollection(),
            "conversation_content": FakeMongoCollection(),
        }

    def collection(self, name: str):
        return self.collections[name]


@pytest.fixture
def mongo():
    return FakeMongo()


@pytest.fixture
def memory_repo(mongo):
    return MemoryMongoRepository(mongo=mongo)


@pytest.fixture
def content_repo(mongo):
    return ContentStorageMongoRepository(mongo=mongo)


@pytest.mark.asyncio
async def test_get_room_memory_returns_none_for_missing(memory_repo):
    assert await memory_repo.get_room_memory("missing") is None


@pytest.mark.asyncio
async def test_create_and_get_room_memory(memory_repo):
    memory_id = await memory_repo.create_room_memory(
        {"room_id": "r1", "memory_id": "m1"}
    )

    assert memory_id == "m1"
    assert await memory_repo.get_room_memory("r1") == {
        "room_id": "r1",
        "memory_id": "m1",
        "_id": "id-1",
    }


@pytest.mark.asyncio
async def test_ensure_room_memory_creates_on_first_call(memory_repo):
    doc = await memory_repo.ensure_room_memory("r1", {"memory_id": "m1", "value": 1})

    assert doc["room_id"] == "r1"
    assert doc["memory_id"] == "m1"


@pytest.mark.asyncio
async def test_ensure_room_memory_idempotent(memory_repo):
    await memory_repo.ensure_room_memory("r1", {"memory_id": "first", "value": 1})
    doc = await memory_repo.ensure_room_memory(
        "r1", {"memory_id": "second", "value": 2}
    )

    assert doc["memory_id"] == "first"
    assert doc["value"] == 1


@pytest.mark.asyncio
async def test_upsert_room_memory_insert_preserves_memory_id(memory_repo):
    await memory_repo.upsert_room_memory(
        "r1",
        {"memory_id": "m1", "memory_content": {"conversation_history": []}},
    )

    doc = await memory_repo.get_room_memory("r1")
    assert doc["room_id"] == "r1"
    assert doc["memory_id"] == "m1"


@pytest.mark.asyncio
async def test_upsert_room_memory_insert_has_no_mongo_path_conflicts(memory_repo):
    await memory_repo.upsert_room_memory(
        "r1",
        {
            "memory_id": "m1",
            "memory_content": {"conversation_history": []},
            "conversation_history": [],
        },
    )

    _query, update, _kwargs = memory_repo._memories.update_one_calls[-1]
    assert set(update["$set"]).isdisjoint(update["$setOnInsert"])


@pytest.mark.asyncio
async def test_update_room_memory_by_room_id_returns_true_for_idempotent_match(
    memory_repo,
):
    await memory_repo.create_room_memory(
        {"room_id": "r1", "memory_id": "m1", "status": "same"}
    )

    ok = await memory_repo.update_room_memory_by_room_id("r1", {"status": "same"})

    assert ok is True


@pytest.mark.asyncio
async def test_update_room_memory_by_room_id_does_not_mutate_identity_fields(
    memory_repo,
):
    await memory_repo.create_room_memory(
        {"room_id": "r1", "memory_id": "m1", "status": "old"}
    )

    ok = await memory_repo.update_room_memory_by_room_id(
        "r1",
        {"room_id": "r2", "memory_id": "m2", "_id": "changed", "status": "new"},
    )

    doc = await memory_repo.get_room_memory("r1")
    assert ok is True
    assert doc["room_id"] == "r1"
    assert doc["memory_id"] == "m1"
    assert doc["status"] == "new"
    assert await memory_repo.get_room_memory("r2") is None


@pytest.mark.asyncio
async def test_push_and_trim_conversation_turn_appends_only_top_level(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {"summary": None},
            "conversation_history": [],
        }
    )

    modified, matched = await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "t1", "role": "user", "content": "hello"},
        max_turns=5,
        summary_stub="[User] hello",
        max_summary_chars=100,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert (modified, matched) == (True, True)
    assert doc["conversation_history"][0]["turn_id"] == "t1"
    assert "conversation_history" not in doc["memory_content"]


@pytest.mark.asyncio
async def test_create_room_memory_strips_nested_history(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "summary": "keep",
                "conversation_history": [
                    {"turn_id": "legacy", "role": "user", "content": "old"}
                ],
            },
            "conversation_history": [
                {"turn_id": "direct", "role": "agent", "content": "new"}
            ],
        }
    )

    doc = await memory_repo.get_room_memory("r1")
    assert doc["memory_content"] == {"summary": "keep"}
    assert [turn["turn_id"] for turn in doc["conversation_history"]] == ["direct"]


def test_normalize_room_memory_reads_only_canonical_direct_history():
    state = normalize_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "conversation_history": [],
            "memory_content": {
                "summary": "keep",
                "conversation_history": [
                    {"turn_id": "legacy", "role": "user", "content": "legacy"}
                ],
            },
        }
    )

    assert state.conversation_history == []
    assert state.summary == "keep"


@pytest.mark.asyncio
async def test_push_uses_only_canonical_direct_history(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "summary": "",
                "conversation_history": [
                    {"turn_id": "legacy", "role": "user", "content": "legacy"}
                ],
            },
            "conversation_history": [
                {"turn_id": "direct", "role": "agent", "content": "direct"}
            ],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "new", "role": "user", "content": "new"},
        max_turns=5,
        summary_stub="unused",
        max_summary_chars=100,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert [turn["turn_id"] for turn in doc["conversation_history"]] == [
        "direct",
        "new",
    ]
    assert "conversation_history" not in doc["memory_content"]


@pytest.mark.asyncio
async def test_push_and_trim_does_not_append_summary_without_trim(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {"conversation_history": [], "summary": "existing"},
            "conversation_history": [],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "t1", "role": "user", "content": "hello"},
        max_turns=5,
        summary_stub="[User] hello",
        max_summary_chars=100,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert doc["memory_content"]["summary"] == "existing"


@pytest.mark.asyncio
async def test_push_and_trim_summary_uses_evicted_turn_preview(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "old", "role": "user", "content": "old content"}
                ],
                "summary": "",
            },
            "conversation_history": [
                {"turn_id": "old", "role": "user", "content": "old content"}
            ],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "new", "role": "user", "content": "new content"},
        max_turns=1,
        summary_stub="[User] new content",
        max_summary_chars=500,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert [turn["turn_id"] for turn in doc["conversation_history"]] == [
        "old",
        "new",
    ]
    assert "conversation_history" not in doc["memory_content"]
    assert doc["memory_content"]["summary"] == "[User] old content..."


@pytest.mark.asyncio
async def test_push_and_trim_summary_ignores_non_string_legacy_content(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "old", "role": "user", "content": {"text": "old"}}
                ],
                "summary": "",
            },
            "conversation_history": [
                {"turn_id": "old", "role": "user", "content": {"text": "old"}}
            ],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "new", "role": "user", "content": "new content"},
        max_turns=1,
        summary_stub="[User] new content",
        max_summary_chars=500,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert doc["memory_content"]["summary"] == "[User] [compact turn]..."


@pytest.mark.asyncio
async def test_summary_tracks_duplicate_turns_without_ids_by_occurrence(memory_repo):
    duplicate = {"role": "user", "content": "duplicate legacy content"}
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [deepcopy(duplicate), deepcopy(duplicate)],
                "summary": "",
            },
            "conversation_history": [deepcopy(duplicate)],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "new", "role": "user", "content": "new"},
        max_turns=2,
        summary_stub="new preview",
        max_summary_chars=500,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert doc["memory_content"]["summary"] == ""


@pytest.mark.asyncio
async def test_summary_matches_turn_id_across_representation_changes(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {
                        "turn_id": "same",
                        "role": "user",
                        "representation": "full",
                        "content": "already summarized content",
                    }
                ],
                "summary": "existing summary",
            },
            "conversation_history": [
                {
                    "turn_id": "same",
                    "role": "user",
                    "representation": "compact",
                    "content": None,
                    "content_ref": {"document_id": "same"},
                }
            ],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "new", "role": "user", "content": "new"},
        max_turns=2,
        summary_stub="new preview",
        max_summary_chars=500,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert doc["memory_content"]["summary"] == "existing summary"
    assert doc["conversation_history"][0]["representation"] == "compact"
    assert "conversation_history" not in doc["memory_content"]


@pytest.mark.asyncio
async def test_push_and_trim_appends_summary_only_when_trimmed_and_keeps_tail(
    memory_repo,
):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "t1"},
                    {"turn_id": "t2"},
                ],
                "summary": "oldest summary text",
            },
            "conversation_history": [
                {"turn_id": "t1"},
                {"turn_id": "t2"},
            ],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "t3", "role": "user", "content": "new"},
        max_turns=2,
        summary_stub="[User] latest important summary",
        max_summary_chars=24,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert [turn["turn_id"] for turn in doc["conversation_history"]] == [
        "t1",
        "t2",
        "t3",
    ]
    assert "conversation_history" not in doc["memory_content"]
    assert doc["memory_content"]["summary"].startswith("...")
    assert "compact turn" in doc["memory_content"]["summary"]
    assert "latest important summary" not in doc["memory_content"]["summary"]


@pytest.mark.asyncio
async def test_twenty_first_short_turn_survives_and_triggers_default_compaction(
    memory_repo, content_repo
):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {"conversation_history": [], "summary": ""},
            "conversation_history": [],
        }
    )

    for index in range(1, 22):
        await memory_repo.push_and_trim_conversation_turn(
            "r1",
            {
                "turn_id": f"t{index}",
                "role": "user",
                "representation": "full",
                "content": f"short message {index}",
                "content_type": "text",
                "estimated_tokens_full": 3,
                "estimated_tokens_compact": 20,
            },
            max_turns=20,
            summary_stub=f"new turn preview {index}",
            max_summary_chars=1000,
        )

    doc = await memory_repo.get_room_memory("r1")
    assert len(doc["conversation_history"]) == 21
    assert "conversation_history" not in doc["memory_content"]
    assert doc["conversation_history"][0]["turn_id"] == "t1"
    assert "short message 1" in doc["memory_content"]["summary"]
    assert "short message 2" not in doc["memory_content"]["summary"]
    assert "short message 21" not in doc["memory_content"]["summary"]
    assert "new turn preview 21" not in doc["memory_content"]["summary"]
    assert len(normalize_room_memory(doc).conversation_history) == 21

    default_config = CompactionConfig(
        enabled=True,
        max_total_tokens=80_000,
        preserve_recent_turns=10,
        content_ttl_days=0,
        concurrency=2,
    )
    assert default_config.max_full_turns == 20
    result = await compaction.compact_if_needed(
        repository=memory_repo,
        content_repository=content_repo,
        room_id="r1",
        config=default_config,
        now=lambda: datetime.now(UTC),
    )

    assert result is not None
    assert result.compacted_count == 11
    compacted_doc = await memory_repo.get_room_memory("r1")
    assert len(compacted_doc["conversation_history"]) == 21
    assert "conversation_history" not in compacted_doc["memory_content"]
    assert all(
        turn["representation"] == "compact"
        for turn in compacted_doc["conversation_history"][:11]
    )
    assert [
        turn["turn_id"]
        for turn in compacted_doc["conversation_history"][-10:]
        if turn["representation"] == "full"
    ] == [f"t{index}" for index in range(12, 22)]


@pytest.mark.asyncio
async def test_push_and_trim_if_absent_rejects_duplicate(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {"summary": None},
            "conversation_history": [{"turn_id": "t1"}],
        }
    )

    result = await memory_repo.push_and_trim_conversation_turn_if_absent(
        "r1",
        {"turn_id": "t1", "role": "user", "content": "duplicate"},
        turn_id="t1",
        max_turns=5,
        summary_stub="dup",
        max_summary_chars=100,
    )

    assert result == (False, True, True)


@pytest.mark.asyncio
async def test_push_and_trim_if_absent_duplicate_check_ignores_malformed_entries(
    memory_repo,
):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [None, "bad", {"turn_id": "t1"}]
            },
            "conversation_history": [
                None,
                {"content": "missing id"},
                {"turn_id": "t1"},
            ],
        }
    )

    result = await memory_repo.push_and_trim_conversation_turn_if_absent(
        "r1",
        {"turn_id": "t1", "role": "user", "content": "duplicate"},
        turn_id="t1",
        max_turns=5,
        summary_stub="dup",
        max_summary_chars=100,
    )

    assert result == (False, True, True)


@pytest.mark.asyncio
async def test_push_and_trim_if_absent_handles_concurrent_duplicate_race(
    memory_repo, monkeypatch
):
    doc = {
        "room_id": "r1",
        "memory_id": "m1",
        "memory_content": {"conversation_history": []},
        "conversation_history": [],
    }
    pushes = []

    async def fake_push(
        room_id, turn, max_turns, summary_stub, max_summary_chars, *, query=None
    ):
        pushes.append(turn)
        if len(pushes) == 2:
            doc["conversation_history"].append({"turn_id": "t1"})
        return None

    async def fake_get(room_id):
        return deepcopy(doc)

    monkeypatch.setattr(memory_repo, "_push_turn", fake_push)
    monkeypatch.setattr(memory_repo, "get_room_memory", fake_get)

    result = await memory_repo.push_and_trim_conversation_turn_if_absent(
        "r1",
        {"turn_id": "t1", "role": "user", "content": "hello"},
        turn_id="t1",
        max_turns=5,
        summary_stub="[User] hello",
        max_summary_chars=500,
    )

    assert result == (False, True, True)


@pytest.mark.asyncio
async def test_push_and_trim_if_absent_missing_room(memory_repo):
    result = await memory_repo.push_and_trim_conversation_turn_if_absent(
        "missing",
        {"turn_id": "t1"},
        turn_id="t1",
        max_turns=5,
        summary_stub="stub",
        max_summary_chars=100,
    )

    assert result == (False, False, False)


@pytest.mark.asyncio
async def test_update_turn_notes(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {"conversation_history": [{"turn_id": "t1"}]},
            "conversation_history": [{"turn_id": "t1"}],
        }
    )

    assert await memory_repo.update_turn_notes("r1", "t1", {"one_liner": "hi"})
    doc = await memory_repo.get_room_memory("r1")
    assert doc["conversation_history"][0]["turn_notes"] == {"one_liner": "hi"}
    assert "conversation_history" not in doc["memory_content"]


@pytest.mark.asyncio
async def test_compact_turns_bulk(memory_repo, mongo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "t1", "representation": "full", "content": "one"},
                    {"turn_id": "t2", "representation": "full", "content": "two"},
                ]
            },
            "conversation_history": [
                {"turn_id": "t1", "representation": "full", "content": "one"},
                {"turn_id": "t2", "representation": "full", "content": "two"},
            ],
            "total_compactions": 0,
        }
    )

    ok = await memory_repo.compact_turns_bulk(
        "r1",
        [
            {
                "turn_id": "t1",
                "content_ref": {"document_id": "d1"},
                "estimated_tokens_compact": 7,
            },
            {
                "turn_id": "t2",
                "content_ref": {"document_id": "d2"},
                "estimated_tokens_compact": 8,
            },
        ],
    )

    doc = await memory_repo.get_room_memory("r1")
    assert ok is True
    assert len(mongo.collections["room_memories"].update_one_calls) == 1
    update = mongo.collections["room_memories"].update_one_calls[0][1]
    assert isinstance(update, list)
    assert update[0]["$set"]["last_activity_at"] == "$$NOW"
    assert doc["total_compactions"] == 1
    assert [t["representation"] for t in doc["conversation_history"]] == [
        "compact",
        "compact",
    ]
    assert [t["content_ref"]["document_id"] for t in doc["conversation_history"]] == [
        "d1",
        "d2",
    ]


@pytest.mark.asyncio
async def test_compact_turns_bulk_skips_already_compact_db_turn(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {
                        "turn_id": "t1",
                        "representation": "compact",
                        "content": None,
                        "content_ref": {"document_id": "existing"},
                    }
                ]
            },
            "conversation_history": [
                {
                    "turn_id": "t1",
                    "representation": "compact",
                    "content": None,
                    "content_ref": {"document_id": "existing"},
                }
            ],
            "total_compactions": 0,
        }
    )

    ok = await memory_repo.compact_turns_bulk(
        "r1",
        [
            {
                "turn_id": "t1",
                "content_ref": {"document_id": "stale"},
                "estimated_tokens_compact": 5,
            }
        ],
    )

    doc = await memory_repo.get_room_memory("r1")
    assert ok is False
    assert doc["conversation_history"][0]["content_ref"]["document_id"] == "existing"
    assert doc["total_compactions"] == 0


@pytest.mark.asyncio
async def test_compact_turns_bulk_does_not_fallback_to_legacy_history(
    memory_repo, mongo
):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "t1", "representation": "full", "content": "one"},
                ]
            },
            "total_compactions": 0,
        }
    )

    ok = await memory_repo.compact_turns_bulk(
        "r1",
        [{"turn_id": "t1", "content_ref": {"document_id": "d1"}}],
    )

    doc = await memory_repo.get_room_memory("r1")
    assert ok is False
    assert "conversation_history" not in doc
    assert "conversation_history" not in doc["memory_content"]
    assert len(mongo.collections["room_memories"].update_one_calls) == 1


@pytest.mark.asyncio
async def test_compact_turns_bulk_allows_missing_turn_ids(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "conversation_history": [
                {"turn_id": "t1", "representation": "full", "content": "one"},
            ],
            "total_compactions": 0,
        }
    )

    ok = await memory_repo.compact_turns_bulk(
        "r1",
        [
            {"turn_id": "t1", "content_ref": {"document_id": "d1"}},
            {"turn_id": "missing", "content_ref": {"document_id": "d-missing"}},
        ],
    )

    doc = await memory_repo.get_room_memory("r1")
    assert ok is True
    assert doc["total_compactions"] == 1
    assert doc["conversation_history"][0]["representation"] == "compact"
    assert doc["conversation_history"][0]["content_ref"]["document_id"] == "d1"


@pytest.mark.asyncio
async def test_compact_turns_bulk_returns_false_on_atomic_write_failure(
    memory_repo, mongo
):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "conversation_history": [
                {"turn_id": "t1", "representation": "full", "content": "one"},
            ],
        }
    )
    mongo.collections["room_memories"].raise_on_update_one = True

    ok = await memory_repo.compact_turns_bulk(
        "r1",
        [{"turn_id": "t1", "content_ref": {"document_id": "d1"}}],
    )

    assert ok is False


@pytest.mark.asyncio
async def test_compact_turns_bulk_returns_false_on_mongo_write_failure(
    memory_repo, mongo
):
    class MongoWriteFailure(Exception):
        pass

    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "conversation_history": [
                {"turn_id": "t1", "representation": "full", "content": "one"},
            ],
        }
    )
    mongo.collections["room_memories"].update_one_exception = MongoWriteFailure(
        "write failed"
    )

    ok = await memory_repo.compact_turns_bulk(
        "r1",
        [{"turn_id": "t1", "content_ref": {"document_id": "d1"}}],
    )

    assert ok is False


@pytest.mark.asyncio
async def test_content_upsert_and_get(content_repo):
    stored = await content_repo.upsert_full_content(
        document_id="doc1",
        room_id="r1",
        turn_id="t1",
        content="hello",
        content_type="text",
        content_hash="hash",
        stored_at=datetime.now(UTC),
    )

    assert stored == "doc1"
    assert (await content_repo.get_content_by_turn_id("r1", "t1"))["content"] == "hello"


@pytest.mark.asyncio
async def test_content_upsert_replaces_existing_content_and_expiry(content_repo):
    first_expiry = datetime(2026, 1, 1, tzinfo=UTC)
    second_expiry = datetime(2026, 1, 2, tzinfo=UTC)

    await content_repo.upsert_full_content(
        document_id="doc-old",
        room_id="r1",
        turn_id="t1",
        content="old",
        content_type="text",
        content_hash="old-hash",
        stored_at=first_expiry,
        expires_at=first_expiry,
        turn_notes={"version": "old"},
    )
    stored = await content_repo.upsert_full_content(
        document_id="doc-new",
        room_id="r1",
        turn_id="t1",
        content="new",
        content_type="text/markdown",
        content_hash="new-hash",
        stored_at=second_expiry,
        expires_at=second_expiry,
        turn_notes={"version": "new"},
    )

    doc = await content_repo.get_content_by_turn_id("r1", "t1")
    assert stored == "doc-new"
    assert doc["document_id"] == "doc-new"
    assert doc["content"] == "new"
    assert doc["content_type"] == "text/markdown"
    assert doc["content_hash"] == "new-hash"
    assert doc["stored_at"] == second_expiry
    assert doc["expires_at"] == second_expiry
    assert doc["turn_notes"] == {"version": "new"}


@pytest.mark.asyncio
async def test_content_get_by_document_id_with_legacy_fallback(content_repo, mongo):
    await mongo.collections["conversation_content"].insert_one(
        {"_id": "legacy-id", "room_id": "r1", "turn_id": "t1", "content": "legacy"}
    )

    assert (await content_repo.get_content_by_document_id("legacy-id"))[
        "content"
    ] == "legacy"


@pytest.mark.asyncio
async def test_content_delete_by_room(content_repo, mongo):
    coll = mongo.collections["conversation_content"]
    await coll.insert_one({"room_id": "r1", "turn_id": "t1"})
    await coll.insert_one({"room_id": "r1", "turn_id": "t2"})
    await coll.insert_one({"room_id": "r2", "turn_id": "t3"})

    assert await content_repo.delete_content_by_room_id("r1") == 2
    assert len(coll.documents) == 1


@pytest.mark.asyncio
async def test_content_stats(content_repo, mongo):
    coll = mongo.collections["conversation_content"]
    await coll.insert_one({"room_id": "r1", "content_type": "text", "content": "abcd"})
    await coll.insert_one({"room_id": "r1", "content_type": "text", "content": "ef"})

    stats = await content_repo.get_content_stats_for_room("r1")

    assert stats["total_documents"] == 2
    assert stats["by_type"]["text"]["size_bytes"] == 6


@pytest.mark.asyncio
async def test_content_text_search_projection_excludes_full_content(
    content_repo, mongo
):
    await content_repo.text_search("r1", "query", limit=10)

    _query, kwargs = mongo.collections["conversation_content"].find_calls[-1]
    projection = kwargs["projection"]
    assert "content" not in projection
    assert projection["turn_id"] == 1
    assert projection["turn_notes"] == 1
    assert projection["stored_at"] == 1
    assert kwargs["sort"] == [
        ("score", {"$meta": "textScore"}),
        ("turn_timestamp", -1),
        ("stored_at", -1),
        ("turn_id", 1),
    ]


@pytest.mark.asyncio
async def test_content_text_search_filters_expired_documents(content_repo, mongo):
    await content_repo.text_search("r1", "query", limit=10)

    query, _kwargs = mongo.collections["conversation_content"].find_calls[-1]
    expiry_filter = query["$or"]

    assert {"expires_at": {"$exists": False}} in expiry_filter
    assert {"expires_at": None} in expiry_filter
    assert any(
        isinstance(item.get("expires_at"), dict) and "$gt" in item["expires_at"]
        for item in expiry_filter
    )


@pytest.mark.asyncio
async def test_content_scan_text_search_caps_lightweight_candidates_without_nin(
    content_repo, mongo
):
    await content_repo.scan_text_search("r1", "query", 250)

    query, kwargs = mongo.collections["conversation_content"].find_calls[-1]
    assert "turn_id" not in query
    assert kwargs["limit"] == 250
    assert "exhaust" not in kwargs
    assert "content" not in kwargs["projection"]


@pytest.mark.asyncio
async def test_content_hydrate_turn_content_filters_expired_documents(
    content_repo, mongo
):
    coll = mongo.collections["conversation_content"]
    await coll.insert_one(
        {
            "room_id": "r1",
            "turn_id": "expired",
            "turn_notes": {"one_liner": "expired"},
            "expires_at": datetime.now(UTC) - timedelta(days=1),
        }
    )
    await coll.insert_one(
        {
            "room_id": "r1",
            "turn_id": "active",
            "turn_notes": {"one_liner": "active"},
            "expires_at": datetime.now(UTC) + timedelta(days=1),
        }
    )

    docs = await content_repo.hydrate_turn_content("r1", ["expired", "active"])

    assert [doc["turn_id"] for doc in docs] == ["active"]
    _query, kwargs = coll.find_calls[-1]
    assert kwargs["projection"]["content"] == 1


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(doc, item) for item in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(doc, item) for item in expected):
                return False
            continue
        actual = _get_path(doc, key)
        if isinstance(expected, dict):
            if "$elemMatch" in expected:
                if not isinstance(actual, list):
                    return False
                if not any(
                    isinstance(item, dict) and _matches(item, expected["$elemMatch"])
                    for item in actual
                ):
                    return False
            elif "$ne" in expected:
                values = _values_for_path(doc, key)
                if any(value == expected["$ne"] for value in values):
                    return False
            elif "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif "$gt" in expected:
                if actual is None or actual <= expected["$gt"]:
                    return False
            elif "$exists" in expected:
                exists = actual is not None
                if exists is not bool(expected["$exists"]):
                    return False
            else:
                return False
        elif actual != expected:
            values = _values_for_path(doc, key)
            if expected not in values:
                return False
    return True


def _get_path(doc: dict, path: str):
    current = doc
    for part in path.split("."):
        if isinstance(current, list):
            return None
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _values_for_path(doc: dict, path: str) -> list:
    parts = path.split(".")
    values = [doc]
    for part in parts:
        next_values = []
        for value in values:
            if isinstance(value, list):
                next_values.extend(
                    item.get(part) for item in value if isinstance(item, dict)
                )
            elif isinstance(value, dict):
                found = value.get(part)
                if isinstance(found, list):
                    next_values.extend(found)
                else:
                    next_values.append(found)
        values = next_values
    return values


def _apply_update(
    doc: dict, update: dict | list[dict], *, array_filters=None, is_insert=False
) -> None:
    if isinstance(update, list):
        for stage in update:
            stage_source = deepcopy(doc)
            for path, value in stage.get("$set", {}).items():
                if path.endswith("conversation_history"):
                    _set_path(
                        doc,
                        path,
                        _eval_history_expression(stage_source, value),
                    )
                elif path == "memory_content.summary":
                    if "$cond" in value or "$let" in value:
                        _set_path(
                            doc, path, _eval_summary_expression(stage_source, value)
                        )
                    else:
                        current = _get_path(doc, path) or ""
                        stub = value["$substrCP"][0]["$concat"][2]
                        _set_path(
                            doc, path, (current + "\n" + stub)[: value["$substrCP"][2]]
                        )
                elif path == "last_activity_at":
                    _set_path(doc, path, "$$NOW")
                elif path == "total_messages":
                    _set_path(doc, path, (_get_path(doc, path) or 0) + 1)
                elif path == "total_compactions":
                    _set_path(doc, path, (_get_path(doc, path) or 0) + 1)
        return
    if is_insert:
        for key, value in update.get("$setOnInsert", {}).items():
            _set_path(doc, key, deepcopy(value))
    for key, value in update.get("$set", {}).items():
        if "$[turn]" in key:
            _set_array_filtered(doc, key, value, array_filters)
        elif ".$." in key:
            array_path, _, field_path = key.partition(".$.")
            turn_id = query_turn_id(doc, array_path)
            for turn in _get_path(doc, array_path) or []:
                if turn.get("turn_id") == turn_id:
                    _set_path(turn, field_path, deepcopy(value))
        else:
            _set_path(doc, key, deepcopy(value))
    for key, value in update.get("$inc", {}).items():
        _set_path(doc, key, (_get_path(doc, key) or 0) + value)
    for key, value in update.get("$push", {}).items():
        existing = _get_path(doc, key) or []
        if isinstance(value, dict) and "$each" in value:
            existing.extend(deepcopy(value["$each"]))
            if "$slice" in value:
                existing = existing[value["$slice"] :]
        else:
            existing.append(deepcopy(value))
        _set_path(doc, key, existing)


def _assert_no_update_path_conflicts(update: dict | list[dict]) -> None:
    if not isinstance(update, dict):
        return
    paths: list[str] = []
    for operator in ("$set", "$setOnInsert"):
        paths.extend(update.get(operator, {}).keys())
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if (
                left == right
                or left.startswith(f"{right}.")
                or right.startswith(f"{left}.")
            ):
                raise RuntimeError(
                    f"Mongo update path conflict: {left} conflicts with {right}"
                )


def _eval_summary_expression(doc: dict, expression: dict) -> str:
    outer_vars = expression["$let"]["vars"]
    addition_cond = outer_vars["addition"]["$cond"]
    max_turns = addition_cond[0]["$gte"][1]
    cap_cond = expression["$let"]["in"]["$let"]["in"]["$cond"]
    max_summary_chars = cap_cond[0]["$gt"][1]
    existing = _get_path(doc, "memory_content.summary") or ""
    history = _get_path(doc, "conversation_history")
    canonical = history if isinstance(history, list) else []
    evicted = (
        canonical[len(canonical) - max_turns] if len(canonical) >= max_turns else None
    )
    addition = _turn_summary_preview(evicted) if evicted is not None else ""
    concatenated = (
        existing
        if not addition
        else addition
        if not existing
        else f"{existing}\n{addition}"
    )
    if len(concatenated) > max_summary_chars:
        return "..." + concatenated[-(max_summary_chars - 3) :]
    return concatenated


def _turn_summary_preview(turn: dict) -> str:
    role = turn.get("role")
    if role == "user":
        label = "User"
    elif role == "agent":
        label = turn.get("agent_name") or "Agent"
    elif role == "supervisor":
        label = "Supervisor"
    else:
        label = role or "Unknown"
    content = turn.get("content")
    if not isinstance(content, str):
        content = "[compact turn]"
    return f"[{label}] {content[:200]}..."


def _eval_history_expression(doc: dict, expression) -> list[dict]:
    if _contains_operator(expression, "$map"):
        entries = _compact_entries_from_expression(expression)
        direct = _get_path(doc, "conversation_history")
        turns = deepcopy(direct) if isinstance(direct, list) else []
        for turn in turns:
            if turn.get("turn_id") in entries and turn.get("representation") == "full":
                entry = entries[turn["turn_id"]]
                turn["representation"] = "compact"
                turn["content"] = None
                turn["content_ref"] = deepcopy(entry["content_ref"])
                turn["estimated_tokens_compact"] = entry["estimated_tokens_compact"]
        if "$slice" in expression:
            return turns[expression["$slice"][1] :]
        return turns
    if "$concatArrays" in expression:
        base = _eval_history_expression(doc, expression["$concatArrays"][0])
        return base + deepcopy(expression["$concatArrays"][1])
    if "$slice" in expression:
        source = _eval_history_expression(doc, expression["$slice"][0])
        limit_expr = expression["$slice"][1]
        limit = (
            limit_expr["$multiply"][0] * limit_expr["$multiply"][1]
            if isinstance(limit_expr, dict) and "$multiply" in limit_expr
            else limit_expr
        )
        return source[limit:]
    if "$cond" in expression:
        direct = _get_path(doc, "conversation_history")
        return deepcopy(direct) if isinstance(direct, list) else []
    if isinstance(expression, str):
        return deepcopy(_get_path(doc, expression.lstrip("$")) or [])
    raise AssertionError(f"Unsupported history expression: {expression}")


def _contains_operator(expression, operator: str) -> bool:
    if isinstance(expression, dict):
        return operator in expression or any(
            _contains_operator(value, operator) for value in expression.values()
        )
    if isinstance(expression, list):
        return any(_contains_operator(value, operator) for value in expression)
    return False


def _set_array_filtered(doc: dict, path: str, value, array_filters) -> None:
    filter_turn_id = array_filters[0]["turn.turn_id"]
    array_path, _, field_path = path.partition(".$[turn].")
    turns = _get_path(doc, array_path) or []
    for turn in turns:
        if turn.get("turn_id") == filter_turn_id:
            _set_path(turn, field_path, deepcopy(value))


def _set_path(doc: dict, path: str, value) -> None:
    current = doc
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def query_turn_id(doc: dict, array_path: str) -> str | None:
    for turn in _get_path(doc, array_path) or []:
        return turn.get("turn_id")
    return None


def _compact_entries_from_expression(expression: dict) -> dict[str, dict]:
    map_expression = _find_operator(expression, "$map")
    patch = map_expression["$map"]["in"]["$cond"][1]["$mergeObjects"][1]
    ref_branches = patch["content_ref"]["$switch"]["branches"]
    token_branches = patch["estimated_tokens_compact"]["$switch"]["branches"]
    entries: dict[str, dict] = {}
    for branch in ref_branches:
        turn_id = branch["case"]["$eq"][1]
        entries.setdefault(turn_id, {})["content_ref"] = branch["then"]
    for branch in token_branches:
        turn_id = branch["case"]["$eq"][1]
        entries.setdefault(turn_id, {})["estimated_tokens_compact"] = branch["then"]
    return entries


def _find_operator(expression: dict, operator: str) -> dict:
    if operator in expression:
        return expression
    for value in expression.values():
        if isinstance(value, dict):
            try:
                return _find_operator(value, operator)
            except KeyError:
                pass
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    try:
                        return _find_operator(item, operator)
                    except KeyError:
                        pass
    raise KeyError(operator)
