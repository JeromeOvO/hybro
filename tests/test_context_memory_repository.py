from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

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

    async def find_one_and_update(self, query: dict, update: dict | list[dict], **kwargs):
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

    async def update_one(self, query: dict, update: dict | list[dict], **kwargs) -> bool:
        self.update_one_calls.append((deepcopy(query), deepcopy(update), deepcopy(kwargs)))
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
                    row = grouped.setdefault(key, {"_id": key, "count": 0, "total_size": 0})
                    row["count"] += 1
                    row["total_size"] += len(doc.get("content", "").encode())
                docs = list(grouped.values())
        return docs

    async def find_one_by_stable_or_native_id(self, field: str, value: str):
        return await self.find_one({field: value}) or await self.find_one({"_id": value})


class FakeMongo:
    def __init__(self):
        self.collections = {
            "room_memories": FakeMongoCollection(),
            "user_memories": FakeMongoCollection(),
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
    memory_id = await memory_repo.create_room_memory({"room_id": "r1", "memory_id": "m1"})

    assert memory_id == "m1"
    assert await memory_repo.get_room_memory("r1") == {"room_id": "r1", "memory_id": "m1", "_id": "id-1"}


@pytest.mark.asyncio
async def test_ensure_room_memory_creates_on_first_call(memory_repo):
    doc = await memory_repo.ensure_room_memory("r1", {"memory_id": "m1", "value": 1})

    assert doc["room_id"] == "r1"
    assert doc["memory_id"] == "m1"


@pytest.mark.asyncio
async def test_ensure_room_memory_idempotent(memory_repo):
    await memory_repo.ensure_room_memory("r1", {"memory_id": "first", "value": 1})
    doc = await memory_repo.ensure_room_memory("r1", {"memory_id": "second", "value": 2})

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
async def test_update_room_memory_by_room_id_returns_true_for_idempotent_match(memory_repo):
    await memory_repo.create_room_memory(
        {"room_id": "r1", "memory_id": "m1", "status": "same"}
    )

    ok = await memory_repo.update_room_memory_by_room_id("r1", {"status": "same"})

    assert ok is True


@pytest.mark.asyncio
async def test_update_room_memory_by_room_id_does_not_mutate_identity_fields(memory_repo):
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
async def test_push_and_trim_conversation_turn_appends(memory_repo):
    await memory_repo.create_room_memory({"room_id": "r1", "memory_id": "m1"})

    modified, matched = await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "t1", "role": "user", "content": "hello"},
        max_turns=5,
        summary_stub="[User] hello",
        max_summary_chars=100,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert (modified, matched) == (True, True)
    assert doc["memory_content"]["conversation_history"][0]["turn_id"] == "t1"


@pytest.mark.asyncio
async def test_push_and_trim_seeds_direct_history_from_legacy_nested_history(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "legacy", "role": "user", "content": "old"}
                ],
                "summary": "",
            },
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "new", "role": "user", "content": "new"},
        max_turns=5,
        summary_stub="[User] new",
        max_summary_chars=100,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert [turn["turn_id"] for turn in doc["memory_content"]["conversation_history"]] == [
        "legacy",
        "new",
    ]
    assert [turn["turn_id"] for turn in doc["conversation_history"]] == [
        "legacy",
        "new",
    ]
    assert [turn.turn_id for turn in normalize_room_memory(doc).conversation_history] == [
        "legacy",
        "new",
    ]


@pytest.mark.asyncio
async def test_push_and_trim_uses_fuller_legacy_history_when_direct_history_is_stale(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "legacy-1", "role": "user", "content": "old one"},
                    {"turn_id": "legacy-2", "role": "agent", "content": "old two"},
                ],
                "summary": "",
            },
            "conversation_history": [
                {"turn_id": "legacy-2", "role": "agent", "content": "old two"}
            ],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "new", "role": "user", "content": "new"},
        max_turns=5,
        summary_stub="[User] new",
        max_summary_chars=100,
    )

    doc = await memory_repo.get_room_memory("r1")
    expected = ["legacy-1", "legacy-2", "new"]
    assert [turn["turn_id"] for turn in doc["memory_content"]["conversation_history"]] == expected
    assert [turn["turn_id"] for turn in doc["conversation_history"]] == expected
    assert [turn.turn_id for turn in normalize_room_memory(doc).conversation_history] == expected


def test_normalize_room_memory_prefers_fuller_legacy_history_when_direct_history_is_stale():
    state = normalize_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "conversation_history": [
                {"turn_id": "direct-1", "role": "user", "content": "direct"}
            ],
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "legacy-1", "role": "user", "content": "legacy one"},
                    {"turn_id": "legacy-2", "role": "agent", "content": "legacy two"},
                ]
            },
        }
    )

    assert [turn.turn_id for turn in state.conversation_history] == [
        "legacy-1",
        "legacy-2",
        "direct-1",
    ]


@pytest.mark.asyncio
async def test_push_and_trim_reconciles_equal_length_divergent_histories(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "legacy-only", "role": "user", "content": "legacy"}
                ],
                "summary": "",
            },
            "conversation_history": [
                {"turn_id": "direct-only", "role": "agent", "content": "direct"}
            ],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "new", "role": "user", "content": "new"},
        max_turns=5,
        summary_stub="[User] new",
        max_summary_chars=100,
    )

    doc = await memory_repo.get_room_memory("r1")
    expected = ["legacy-only", "direct-only", "new"]
    assert [turn["turn_id"] for turn in doc["memory_content"]["conversation_history"]] == expected
    assert [turn["turn_id"] for turn in doc["conversation_history"]] == expected
    assert [turn.turn_id for turn in normalize_room_memory(doc).conversation_history] == expected


def test_normalize_room_memory_reconciles_equal_length_divergent_histories():
    state = normalize_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "conversation_history": [
                {"turn_id": "direct-only", "role": "agent", "content": "direct"}
            ],
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "legacy-only", "role": "user", "content": "legacy"}
                ]
            },
        }
    )

    assert [turn.turn_id for turn in state.conversation_history] == [
        "legacy-only",
        "direct-only",
    ]


@pytest.mark.asyncio
async def test_push_and_trim_prefers_direct_history_for_duplicate_turn_ids(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {
                        "turn_id": "same",
                        "role": "user",
                        "representation": "compact",
                        "content_ref": {"document_id": "old"},
                    }
                ],
                "summary": "",
            },
            "conversation_history": [
                {
                    "turn_id": "same",
                    "role": "user",
                    "representation": "full",
                    "content": "new direct content",
                    "turn_notes": {"one_liner": "new"},
                }
            ],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "new", "role": "user", "content": "new"},
        max_turns=5,
        summary_stub="[User] new",
        max_summary_chars=200,
    )

    doc = await memory_repo.get_room_memory("r1")
    first_turn = doc["conversation_history"][0]
    assert first_turn["turn_id"] == "same"
    assert first_turn["representation"] == "full"
    assert first_turn["content"] == "new direct content"
    assert first_turn["turn_notes"] == {"one_liner": "new"}


def test_normalize_room_memory_prefers_direct_history_for_duplicate_turn_ids():
    state = normalize_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {
                        "turn_id": "same",
                        "role": "user",
                        "representation": "compact",
                        "content_ref": {"document_id": "old"},
                    }
                ]
            },
            "conversation_history": [
                {
                    "turn_id": "same",
                    "role": "user",
                    "representation": "full",
                    "content": "new direct content",
                    "turn_notes": {"one_liner": "new"},
                }
            ],
        }
    )

    assert len(state.conversation_history) == 1
    assert state.conversation_history[0].representation == "full"
    assert state.conversation_history[0].content == "new direct content"
    assert state.conversation_history[0].turn_notes == {"one_liner": "new"}


@pytest.mark.asyncio
async def test_push_and_trim_keeps_multiple_no_id_turns(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {"role": "user", "content": "legacy without id"}
                ],
                "summary": "",
            },
            "conversation_history": [
                {"role": "agent", "content": "direct without id"}
            ],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "new", "role": "user", "content": "new"},
        max_turns=5,
        summary_stub="[User] new",
        max_summary_chars=200,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert [turn.get("content") for turn in doc["conversation_history"]] == [
        "legacy without id",
        "direct without id",
        "new",
    ]


@pytest.mark.asyncio
async def test_reconciliation_trim_appends_caller_summary_stub(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "legacy-1", "role": "user", "content": "legacy one"},
                    {"turn_id": "legacy-2", "role": "user", "content": "legacy two"},
                ],
                "summary": "",
            },
            "conversation_history": [
                {"turn_id": "direct-1", "role": "agent", "content": "direct one"},
                {"turn_id": "direct-2", "role": "agent", "content": "direct two"},
            ],
        }
    )

    await memory_repo.push_and_trim_conversation_turn(
        "r1",
        {"turn_id": "new", "role": "user", "content": "new"},
        max_turns=2,
        summary_stub="[User] new",
        max_summary_chars=1000,
    )

    doc = await memory_repo.get_room_memory("r1")
    assert [turn["turn_id"] for turn in doc["conversation_history"]] == [
        "direct-2",
        "new",
    ]
    summary = doc["memory_content"]["summary"]
    assert summary == "[User] new"
    assert "legacy one" not in summary
    assert "legacy two" not in summary
    assert "direct one" not in summary


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
async def test_push_and_trim_summary_uses_caller_stub_when_trimmed(memory_repo):
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
    assert [turn["turn_id"] for turn in doc["conversation_history"]] == ["new"]
    assert doc["memory_content"]["summary"] == "[User] new content"


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
    assert doc["memory_content"]["summary"] == "[User] new content"


@pytest.mark.asyncio
async def test_push_and_trim_appends_summary_only_when_trimmed_and_keeps_tail(memory_repo):
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
    assert [turn["turn_id"] for turn in doc["conversation_history"]] == ["t2", "t3"]
    assert doc["memory_content"]["summary"].startswith("...")
    assert "important summary" in doc["memory_content"]["summary"]
    assert "[compact turn]" not in doc["memory_content"]["summary"]
    assert doc["memory_content"]["summary"].endswith("important summary")


@pytest.mark.asyncio
async def test_push_and_trim_if_absent_rejects_duplicate(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {"conversation_history": [{"turn_id": "t1"}]},
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
async def test_push_and_trim_if_absent_duplicate_check_ignores_malformed_entries(memory_repo):
    await memory_repo.create_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {"conversation_history": [None, "bad", {"turn_id": "t1"}]},
            "conversation_history": [None, {"content": "missing id"}],
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
async def test_push_and_trim_if_absent_handles_concurrent_duplicate_race(memory_repo, monkeypatch):
    doc = {
        "room_id": "r1",
        "memory_id": "m1",
        "memory_content": {"conversation_history": []},
        "conversation_history": [],
    }
    pushes = []

    async def fake_push(room_id, turn, max_turns, summary_stub, max_summary_chars, *, query=None):
        pushes.append(turn)
        if len(pushes) == 2:
            doc["memory_content"]["conversation_history"].append({"turn_id": "t1"})
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
    assert doc["memory_content"]["conversation_history"][0]["turn_notes"] == {"one_liner": "hi"}
    assert doc["conversation_history"][0]["turn_notes"] == {"one_liner": "hi"}


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
            {"turn_id": "t1", "content_ref": {"document_id": "d1"}, "estimated_tokens_compact": 7},
            {"turn_id": "t2", "content_ref": {"document_id": "d2"}, "estimated_tokens_compact": 8},
        ],
    )

    doc = await memory_repo.get_room_memory("r1")
    assert ok is True
    assert len(mongo.collections["room_memories"].update_one_calls) == 1
    update = mongo.collections["room_memories"].update_one_calls[0][1]
    assert isinstance(update, list)
    assert update[0]["$set"]["last_activity_at"] == "$$NOW"
    assert doc["total_compactions"] == 1
    assert [t["representation"] for t in doc["conversation_history"]] == ["compact", "compact"]
    assert [t["content_ref"]["document_id"] for t in doc["conversation_history"]] == ["d1", "d2"]


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
async def test_compact_turns_bulk_preserves_absent_history_shapes(memory_repo, mongo):
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

    update = mongo.collections["room_memories"].update_one_calls[0][1]
    direct_history_expression = update[0]["$set"]["conversation_history"]
    doc = await memory_repo.get_room_memory("r1")
    assert ok is True
    assert direct_history_expression["$cond"][2] == "$$REMOVE"
    assert "conversation_history" not in doc
    assert doc["memory_content"]["conversation_history"][0]["representation"] == "compact"


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
async def test_compact_turns_bulk_returns_false_on_atomic_write_failure(memory_repo, mongo):
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
async def test_compact_turns_bulk_returns_false_on_mongo_write_failure(memory_repo, mongo):
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
    mongo.collections["room_memories"].update_one_exception = MongoWriteFailure("write failed")

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

    assert (await content_repo.get_content_by_document_id("legacy-id"))["content"] == "legacy"


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
async def test_content_text_search_projection_excludes_full_content(content_repo, mongo):
    await content_repo.text_search("r1", "query", limit=10)

    _query, kwargs = mongo.collections["conversation_content"].find_calls[-1]
    projection = kwargs["projection"]
    assert "content" not in projection
    assert projection["turn_id"] == 1
    assert projection["turn_notes"] == 1
    assert projection["stored_at"] == 1


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
async def test_content_hydrate_turn_notes_filters_expired_documents(content_repo, mongo):
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

    docs = await content_repo.hydrate_turn_notes("r1", ["expired", "active"])

    assert [doc["turn_id"] for doc in docs] == ["active"]


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
                next_values.extend(item.get(part) for item in value if isinstance(item, dict))
            elif isinstance(value, dict):
                found = value.get(part)
                if isinstance(found, list):
                    next_values.extend(found)
                else:
                    next_values.append(found)
        values = next_values
    return values


def _apply_update(doc: dict, update: dict | list[dict], *, array_filters=None, is_insert=False) -> None:
    if isinstance(update, list):
        for stage in update:
            stage_source = deepcopy(doc)
            for path, value in stage.get("$set", {}).items():
                if path.endswith("conversation_history") and ("$map" in value or "$cond" in value):
                    entries = _compact_entries_from_expression(value)
                    turns = _get_path(doc, path) or []
                    for turn in turns:
                        if (
                            turn.get("turn_id") in entries
                            and turn.get("representation") == "full"
                        ):
                            entry = entries[turn["turn_id"]]
                            turn["representation"] = "compact"
                            turn["content"] = None
                            turn["content_ref"] = deepcopy(entry["content_ref"])
                            turn["estimated_tokens_compact"] = entry["estimated_tokens_compact"]
                elif path.endswith("conversation_history"):
                    if "$let" in value:
                        _set_path(doc, path, _eval_history_append_expression(stage_source, value))
                    elif "$concatArrays" in value:
                        existing = _get_path(stage_source, path) or []
                        turn = value["$concatArrays"][1][0]
                        _set_path(doc, path, existing + [deepcopy(turn)])
                    elif "$slice" in value:
                        source_path = value["$slice"][0].lstrip("$")
                        source = _get_path(stage_source, source_path) or []
                        limit_expr = value["$slice"][1]
                        if isinstance(limit_expr, dict) and "$multiply" in limit_expr:
                            limit = limit_expr["$multiply"][0] * limit_expr["$multiply"][1]
                        else:
                            limit = limit_expr
                        _set_path(doc, path, deepcopy(source[limit:]))
                elif path == "memory_content.summary":
                    if "$cond" in value:
                        _set_path(doc, path, _eval_summary_expression(stage_source, value))
                    else:
                        current = _get_path(doc, path) or ""
                        stub = value["$substrCP"][0]["$concat"][2]
                        _set_path(doc, path, (current + "\n" + stub)[: value["$substrCP"][2]])
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
        for right in paths[index + 1:]:
            if left == right or left.startswith(f"{right}.") or right.startswith(f"{left}."):
                raise RuntimeError(f"Mongo update path conflict: {left} conflicts with {right}")


def _eval_summary_expression(doc: dict, expression: dict) -> str:
    cond = expression["$cond"]
    max_turns = cond["if"]["$gt"][1]
    existing = _get_path(doc, "memory_content.summary") or ""
    history = _get_path(doc, "memory_content.conversation_history") or []
    if len(history) <= max_turns:
        return existing

    concatenated_expr = cond["then"]["$let"]["in"]["$let"]["vars"]["concatenated"]["$cond"]
    summary_addition = concatenated_expr["then"]
    concatenated = summary_addition if existing == "" else f"{existing}\n{summary_addition}"
    cap_cond = cond["then"]["$let"]["in"]["$let"]["in"]["$cond"]
    max_summary_chars = cap_cond["if"]["$gt"][1]
    if len(concatenated) > max_summary_chars:
        return "..." + concatenated[-(max_summary_chars - 3):]
    return concatenated


def _eval_history_append_expression(doc: dict, expression: dict) -> list[dict]:
    let_expr = expression["$let"]
    primary_path = let_expr["vars"]["primary"]["$ifNull"][0].lstrip("$")
    fallback_path = let_expr["vars"]["fallback"]["$ifNull"][0].lstrip("$")
    primary = _get_path(doc, primary_path) or []
    fallback = _get_path(doc, fallback_path) or []
    turn = let_expr["in"]["$concatArrays"][1][0]
    base = _merge_history_docs(primary, fallback)
    return deepcopy(base) + [deepcopy(turn)]


def _merge_history_docs(primary: list[dict], fallback: list[dict]) -> list[dict]:
    merged = []
    positions_by_turn_id = {}
    for turn in [*primary, *fallback]:
        turn_id = turn.get("turn_id")
        if turn_id:
            if turn_id in positions_by_turn_id:
                merged[positions_by_turn_id[turn_id]] = turn
                continue
            positions_by_turn_id[turn_id] = len(merged)
        merged.append(turn)
    return merged


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
    if "$cond" in expression:
        expression = expression["$cond"][1]
    patch = expression["$map"]["in"]["$cond"][1]["$mergeObjects"][1]
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
