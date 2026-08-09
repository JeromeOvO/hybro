from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts.migrate_conversation_history import (
    MigrationBlocker,
    plan_document,
    run_migration,
)


class FakeCursor:
    def __init__(self, documents):
        self.documents = [deepcopy(document) for document in documents]
        self.offset = 0

    def sort(self, _spec):
        self.documents.sort(key=lambda row: str(row["_id"]))
        return self

    async def to_list(self, *, length):
        rows = self.documents[self.offset : self.offset + length]
        self.offset += len(rows)
        return rows


class FakeCollection:
    def __init__(self, documents):
        self.documents = [deepcopy(document) for document in documents]
        self.update_calls = 0

    def find(self, _query, *, projection):
        assert projection["conversation_history"] == 1
        return FakeCursor(self.documents)

    async def update_one(self, query, update):
        self.update_calls += 1
        for document in self.documents:
            if _matches_snapshot(document, query):
                before = deepcopy(document)
                document["conversation_history"] = deepcopy(
                    update["$set"]["conversation_history"]
                )
                memory_content = document.get("memory_content")
                if isinstance(memory_content, dict):
                    memory_content.pop("conversation_history", None)
                return SimpleNamespace(
                    matched_count=1,
                    modified_count=int(document != before),
                )
        return SimpleNamespace(matched_count=0, modified_count=0)


class FakeDatabase:
    def __init__(self, documents):
        self.collection = FakeCollection(documents)

    def __getitem__(self, name):
        assert name == "room_memories"
        return self.collection


def _matches_snapshot(document, query):
    if document.get("_id") != query["_id"]:
        return False
    for path in ("conversation_history", "memory_content.conversation_history"):
        expected = query[path]
        if path == "conversation_history":
            present = path in document
            actual = document.get(path)
        else:
            memory_content = document.get("memory_content")
            present = (
                isinstance(memory_content, dict)
                and "conversation_history" in memory_content
            )
            actual = memory_content.get("conversation_history") if present else None
        if expected == {"$exists": False}:
            if present:
                return False
        elif not present or actual != expected:
            return False
    return True


def turn(turn_id=None, content="content"):
    value = {"role": "user", "content": content}
    if turn_id is not None:
        value["turn_id"] = turn_id
    return value


@pytest.mark.parametrize(
    ("document", "category", "contents"),
    [
        (
            {
                "_id": "legacy",
                "memory_content": {"conversation_history": [turn("l", "legacy")]},
            },
            "legacy_only",
            ["legacy"],
        ),
        (
            {"_id": "direct", "conversation_history": [turn("d", "direct")]},
            "direct_only",
            ["direct"],
        ),
        (
            {
                "_id": "equal",
                "memory_content": {"conversation_history": [turn("x", "same")]},
                "conversation_history": [turn("x", "same")],
            },
            "equal",
            ["same"],
        ),
        (
            {
                "_id": "divergent",
                "memory_content": {"conversation_history": [turn("l", "legacy")]},
                "conversation_history": [turn("d", "direct")],
            },
            "divergent",
            ["legacy", "direct"],
        ),
    ],
)
def test_plan_document_schema_shapes(document, category, contents):
    plan = plan_document(document)

    assert plan.category == category
    assert [item["content"] for item in plan.history] == contents


def test_plan_document_direct_wins_same_turn_id_at_stable_position():
    plan = plan_document(
        {
            "_id": "conflict",
            "memory_content": {
                "conversation_history": [
                    turn("first", "legacy first"),
                    turn("same", "legacy conflict"),
                ]
            },
            "conversation_history": [
                turn("same", "direct winner"),
                turn("last", "direct last"),
            ],
        }
    )

    assert [(item["turn_id"], item["content"]) for item in plan.history] == [
        ("first", "legacy first"),
        ("same", "direct winner"),
        ("last", "direct last"),
    ]


def test_plan_document_stably_preserves_all_no_id_items():
    plan = plan_document(
        {
            "_id": "no-id",
            "memory_content": {
                "conversation_history": [
                    turn(content="legacy one"),
                    turn(content="legacy two"),
                ]
            },
            "conversation_history": [turn(content="direct one")],
        }
    )

    assert [item["content"] for item in plan.history] == [
        "legacy one",
        "legacy two",
        "direct one",
    ]


@pytest.mark.parametrize(
    "document",
    [
        {"_id": "bad-direct", "conversation_history": "not-an-array"},
        {
            "_id": "bad-item",
            "memory_content": {"conversation_history": ["not-an-object"]},
        },
        {"_id": "bad-content", "memory_content": "not-an-object"},
    ],
)
def test_plan_document_malformed_is_a_blocker(document):
    with pytest.raises(MigrationBlocker):
        plan_document(document)


@pytest.mark.asyncio
async def test_migration_dry_run_is_read_only_and_apply_is_repeatable():
    database = FakeDatabase(
        [
            {
                "_id": "legacy",
                "memory_content": {
                    "summary": "keep me",
                    "conversation_history": [turn("l", "legacy")],
                },
            },
            {"_id": "direct", "conversation_history": [turn("d", "direct")]},
            {
                "_id": "conflict",
                "memory_content": {"conversation_history": [turn("x", "old")]},
                "conversation_history": [turn("x", "new")],
            },
        ]
    )
    original = deepcopy(database.collection.documents)

    dry_run = await run_migration(database, apply=False, batch_size=2)

    assert dry_run.would_update == 2
    assert database.collection.documents == original
    assert database.collection.update_calls == 0

    applied = await run_migration(database, apply=True, batch_size=2)

    assert applied.updated == 2
    assert applied.would_update == 0
    for document in database.collection.documents:
        assert isinstance(document["conversation_history"], list)
        assert "conversation_history" not in document.get("memory_content", {})
    legacy = next(
        row for row in database.collection.documents if row["_id"] == "legacy"
    )
    assert legacy["memory_content"]["summary"] == "keep me"
    conflict = next(
        row for row in database.collection.documents if row["_id"] == "conflict"
    )
    assert conflict["conversation_history"][0]["content"] == "new"

    repeated = await run_migration(database, apply=True, batch_size=2)

    assert repeated.updated == 0
    assert repeated.would_update == 0


@pytest.mark.asyncio
async def test_apply_fails_closed_before_any_write_when_a_blocker_exists():
    database = FakeDatabase(
        [
            {
                "_id": "valid",
                "memory_content": {"conversation_history": [turn("v", "valid")]},
            },
            {"_id": "blocked", "conversation_history": ["invalid"]},
        ]
    )

    with pytest.raises(RuntimeError, match="blockers"):
        await run_migration(database, apply=True, batch_size=1)

    assert database.collection.update_calls == 0
    assert "conversation_history" not in database.collection.documents[0]
