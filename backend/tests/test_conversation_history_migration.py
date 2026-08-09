from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from common.utils.context_utils import estimate_tokens
from context_memory.compaction import estimate_compact_turn_tokens
from context_memory.translators import turn_from_dict
from scripts.migrate_conversation_history import (
    MigrationBlocker,
    _parse_args,
    audit_collection,
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


class FakeRoomCollection:
    def __init__(self, documents, *, before_update=None):
        self.documents = [deepcopy(document) for document in documents]
        self.update_calls = 0
        self.before_update = before_update

    def find(self, _query, *, projection):
        assert projection["conversation_history"] == 1
        assert projection["room_id"] == 1
        return FakeCursor(self.documents)

    async def update_one(self, query, update):
        self.update_calls += 1
        if self.before_update is not None:
            self.before_update(self.documents, query)
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


class FakeContentCollection:
    def __init__(self, documents):
        self.documents = [deepcopy(document) for document in documents]
        self.find_one_calls = 0

    async def find_one(self, query):
        self.find_one_calls += 1
        return next(
            (
                deepcopy(document)
                for document in self.documents
                if all(document.get(key) == value for key, value in query.items())
            ),
            None,
        )


class FakeDatabase:
    def __init__(self, documents, content_documents=None, *, before_update=None):
        self.collection = FakeRoomCollection(documents, before_update=before_update)
        self.content_collection = FakeContentCollection(content_documents or [])

    def __getitem__(self, name):
        if name == "room_memories":
            return self.collection
        assert name == "conversation_content"
        return self.content_collection


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


def test_parser_accepts_only_noncredential_connection_overrides():
    args = _parse_args(
        ["--database", "history_archive", "--batch-size", "25", "--apply"]
    )

    assert args.database == "history_archive"
    assert args.batch_size == 25
    assert args.apply is True
    assert not hasattr(args, "mongo_url")


def test_parser_help_advertises_only_safe_options(capsys):
    with pytest.raises(SystemExit) as exc_info:
        _parse_args(["--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert captured.err == ""
    assert "--apply" in captured.out
    assert "--database" in captured.out
    assert "--batch-size" in captured.out
    assert "--mongo-url" not in captured.out


@pytest.mark.parametrize(
    "arguments",
    [
        ["--mongo-url", "mongodb://user:TOPSECRET@db.example/hybro"],
        ["--mongo-url=mongodb://user:TOPSECRET@db.example/hybro"],
        ["--mongo", "mongodb://user:TOPSECRET@db.example/hybro"],
        ["--mongo-urlx=mongodb://user:TOPSECRET@db.example/hybro"],
        ["--batch-sze", "mongodb://user:TOPSECRET@db.example/hybro"],
        ["mongodb://user:TOPSECRET@db.example/hybro"],
        ["--batch-size", "mongodb://user:TOPSECRET@db.example/hybro"],
        ["--database"],
    ],
)
def test_parser_errors_never_echo_arguments(arguments, capsys):
    credential = "mongodb://user:TOPSECRET@db.example/hybro"

    with pytest.raises(SystemExit) as exc_info:
        _parse_args(arguments)

    captured = capsys.readouterr()
    assert exc_info.value.code != 0
    assert "TOPSECRET" not in captured.out
    assert "TOPSECRET" not in captured.err
    assert credential not in captured.out
    assert credential not in captured.err
    assert captured.err == "error: invalid command-line arguments\n"


def turn(turn_id=None, content="content"):
    value = {"role": "user", "content": content}
    if turn_id is not None:
        value["turn_id"] = turn_id
    return value


def compact_turn(
    turn_id,
    document_id=None,
    *,
    brief_summary=None,
    one_liner=None,
    estimated_tokens_compact=20,
):
    value = {
        "turn_id": turn_id,
        "role": "user",
        "content": None,
        "representation": "compact",
        "content_ref": {
            "collection": "conversation_content",
            "document_id": document_id,
        },
        "estimated_tokens_compact": estimated_tokens_compact,
    }
    if brief_summary is not None:
        value["brief_summary"] = brief_summary
    if one_liner is not None:
        value["turn_notes"] = {"one_liner": one_liner}
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


@pytest.mark.parametrize(
    ("document", "content_document", "category"),
    [
        (
            {
                "_id": "legacy",
                "room_id": "room-legacy",
                "memory_content": {
                    "conversation_history": [compact_turn("l", "content-l")]
                },
            },
            {
                "document_id": "content-l",
                "room_id": "room-legacy",
                "turn_id": "l",
                "content": "  legacy   compact content  ",
            },
            "legacy_only",
        ),
        (
            {
                "_id": "direct",
                "room_id": "room-direct",
                "conversation_history": [
                    compact_turn("d", "missing-id", brief_summary="   ")
                ],
            },
            {
                "document_id": "different-id",
                "room_id": "room-direct",
                "turn_id": "d",
                "content": "direct fallback by room and turn",
            },
            "direct_only",
        ),
        (
            {
                "_id": "divergent",
                "room_id": "room-divergent",
                "memory_content": {
                    "conversation_history": [compact_turn("same", "old-content")]
                },
                "conversation_history": [
                    compact_turn("same", "winner-content", brief_summary="")
                ],
            },
            {
                "document_id": "winner-content",
                "room_id": "room-divergent",
                "turn_id": "same",
                "content": "direct winner compact content",
            },
            "divergent",
        ),
    ],
)
@pytest.mark.asyncio
async def test_migration_backfills_reconciled_legacy_direct_and_divergent_compact(
    document, content_document, category
):
    database = FakeDatabase([document], [content_document])
    original = deepcopy(database.collection.documents)

    dry_run = await run_migration(database, apply=False, batch_size=1)

    assert getattr(dry_run, category) == 1
    assert dry_run.backfill_count == 1
    assert dry_run.missing_content_blockers == 0
    assert database.collection.documents == original

    applied = await run_migration(database, apply=True, batch_size=1)

    assert applied.updated == 1
    assert applied.backfilled == 1
    migrated = database.collection.documents[0]
    assert "conversation_history" not in migrated.get("memory_content", {})
    summary = migrated["conversation_history"][0]["brief_summary"]
    assert summary in {
        "legacy compact content",
        "direct fallback by room and turn",
        "direct winner compact content",
    }
    migrated_turn = migrated["conversation_history"][0]
    assert migrated_turn["estimated_tokens_compact"] == estimate_tokens(
        turn_from_dict(migrated_turn).to_context_string()
    )

    repeated = await run_migration(database, apply=True, batch_size=1)

    assert repeated.updated == 0
    assert repeated.backfilled == 0


@pytest.mark.parametrize(
    ("document", "category"),
    [
        (
            {
                "_id": "direct-existing-summary",
                "conversation_history": [
                    compact_turn(
                        "direct",
                        "content-direct",
                        brief_summary="direct existing summary " * 12,
                    )
                ],
            },
            "direct_only",
        ),
        (
            {
                "_id": "legacy-existing-summary",
                "memory_content": {
                    "conversation_history": [
                        compact_turn(
                            "legacy",
                            "content-legacy",
                            brief_summary="legacy existing summary " * 12,
                        )
                    ]
                },
            },
            "legacy_only",
        ),
        (
            {
                "_id": "divergent-existing-summary",
                "memory_content": {
                    "conversation_history": [turn("same", "legacy full")]
                },
                "conversation_history": [
                    compact_turn(
                        "same",
                        "content-winner",
                        brief_summary="direct winner existing summary " * 12,
                    )
                ],
            },
            "divergent",
        ),
    ],
)
@pytest.mark.asyncio
async def test_migration_reestimates_existing_compact_summaries_without_content_reads(
    document, category
):
    database = FakeDatabase([document])
    original_turn = (
        document.get("conversation_history")
        or document["memory_content"]["conversation_history"]
    )[0]
    assert original_turn["estimated_tokens_compact"] == 20
    assert estimate_compact_turn_tokens(original_turn) != 20

    dry_run = await run_migration(database, apply=False, batch_size=1)

    assert getattr(dry_run, category) == 1
    assert dry_run.would_update == 1
    assert dry_run.backfill_count == 0
    assert database.content_collection.find_one_calls == 0

    applied = await run_migration(database, apply=True, batch_size=1)

    assert applied.updated == 1
    assert applied.backfilled == 0
    migrated_turn = database.collection.documents[0]["conversation_history"][0]
    assert migrated_turn["estimated_tokens_compact"] == estimate_compact_turn_tokens(
        migrated_turn
    )
    assert database.content_collection.find_one_calls == 0

    repeated = await run_migration(database, apply=True, batch_size=1)

    assert repeated.updated == 0
    assert repeated.would_update == 0
    assert database.content_collection.find_one_calls == 0


@pytest.mark.asyncio
async def test_migration_existing_summary_with_canonical_estimate_is_a_no_op():
    existing = compact_turn(
        "canonical",
        "content-canonical",
        brief_summary="already canonical compact summary",
    )
    existing["estimated_tokens_compact"] = estimate_compact_turn_tokens(existing)
    database = FakeDatabase([{"_id": "canonical", "conversation_history": [existing]}])
    original = deepcopy(database.collection.documents)

    result = await run_migration(database, apply=True, batch_size=1)

    assert result.updated == 0
    assert result.would_update == 0
    assert database.collection.update_calls == 0
    assert database.content_collection.find_one_calls == 0
    assert database.collection.documents == original


@pytest.mark.parametrize(
    "content_ref", [None, {}, "not-an-object", {"document_id": " "}]
)
@pytest.mark.asyncio
async def test_migration_fails_closed_for_invalid_compact_content_ref(content_ref):
    invalid = compact_turn(
        "invalid-ref",
        "placeholder",
        brief_summary="summary does not make an invalid pointer trustworthy",
    )
    if content_ref is None:
        invalid.pop("content_ref")
    else:
        invalid["content_ref"] = content_ref
    database = FakeDatabase([{"_id": "invalid-ref", "conversation_history": [invalid]}])

    with pytest.raises(RuntimeError, match="blockers"):
        await run_migration(database, apply=True, batch_size=1)

    assert database.collection.update_calls == 0
    assert database.content_collection.find_one_calls == 0


@pytest.mark.asyncio
async def test_migration_uses_bounded_existing_one_liner_when_content_is_missing():
    one_liner = "  reliable   note " + ("detail " * 40)
    database = FakeDatabase(
        [
            {
                "_id": "fallback",
                "room_id": "room-fallback",
                "conversation_history": [
                    compact_turn("fallback", "gone", one_liner=one_liner)
                ],
            }
        ]
    )

    result = await run_migration(database, apply=True, batch_size=1)

    summary = database.collection.documents[0]["conversation_history"][0][
        "brief_summary"
    ]
    assert result.backfilled == 1
    assert len(summary) == 200
    assert summary.startswith("reliable note detail")
    assert summary.endswith("...")
    migrated_turn = database.collection.documents[0]["conversation_history"][0]
    assert migrated_turn["estimated_tokens_compact"] == estimate_tokens(
        turn_from_dict(migrated_turn).to_context_string()
    )
    assert migrated_turn["estimated_tokens_compact"] > 20


@pytest.mark.asyncio
async def test_migration_reports_missing_compact_content_as_distinct_blocker(capsys):
    database = FakeDatabase(
        [
            {
                "_id": "missing",
                "room_id": "room-missing",
                "conversation_history": [compact_turn("missing", "expired")],
            }
        ]
    )

    audit = await audit_collection(
        database.collection,
        database.content_collection,
        batch_size=1,
    )

    assert audit.blockers == 1
    assert audit.missing_content_blockers == 1
    assert audit.backfill_count == 0
    assert "missing full content" in capsys.readouterr().out

    with pytest.raises(RuntimeError, match="blockers"):
        await run_migration(database, apply=True, batch_size=1)
    assert database.collection.update_calls == 0
    assert (
        "brief_summary"
        not in database.collection.documents[0]["conversation_history"][0]
    )


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


@pytest.mark.asyncio
async def test_compact_backfill_preserves_optimistic_history_snapshot():
    def concurrent_write(documents, _query):
        documents[0]["conversation_history"].append(turn("concurrent", "new"))

    database = FakeDatabase(
        [
            {
                "_id": "race",
                "room_id": "room-race",
                "conversation_history": [compact_turn("race", "content-race")],
            }
        ],
        [
            {
                "document_id": "content-race",
                "room_id": "room-race",
                "turn_id": "race",
                "content": "recover this summary",
            }
        ],
        before_update=concurrent_write,
    )

    with pytest.raises(RuntimeError, match="snapshot changed during apply"):
        await run_migration(database, apply=True, batch_size=1)

    assert database.collection.update_calls == 1
    history = database.collection.documents[0]["conversation_history"]
    assert [item["turn_id"] for item in history] == ["race", "concurrent"]
    assert "brief_summary" not in history[0]
