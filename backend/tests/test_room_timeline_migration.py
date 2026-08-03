from __future__ import annotations

import os
import subprocess
import sys
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from room.timeline import normalize_timeline_document
from scripts.migrate_room_timeline_sort_keys import audit_collection, run_migration


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.position = 0

    def sort(self, _sort):
        self.rows.sort(key=lambda row: (type(row["_id"]).__name__, repr(row["_id"])))
        return self

    async def to_list(self, length):
        start = self.position
        self.position += length
        return deepcopy(self.rows[start : self.position])


class Collection:
    def __init__(self, name, rows):
        self.name = name
        self.rows = deepcopy(rows)
        self.bulk_calls = 0
        self.update_one_calls = []
        self.delete_one_calls = []

    def find(self, query, projection=None):
        assert query == {}
        return Cursor(list(self.rows))

    async def bulk_write(self, operations, ordered=True):
        modified = 0
        for operation in operations:
            query = operation._filter
            update = operation._doc
            for row in self.rows:
                if (
                    row["_id"] == query["_id"]
                    and row.get("room_id") == query.get("room_id")
                    and row.get("message_id") == query.get("message_id")
                    and row.get("message_created_at") == query.get("message_created_at")
                    and "timeline_sort_us" not in row
                ):
                    row.update(update["$set"])
                    modified += 1
        self.bulk_calls += 1
        return SimpleNamespace(modified_count=modified)

    async def update_one(self, query, update, **kwargs):
        self.update_one_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                row.update(update["$set"])
                return True
        if kwargs.get("upsert"):
            self.rows.append({**deepcopy(query), **deepcopy(update["$set"])})
            return True
        return False

    async def delete_one(self, query):
        self.delete_one_calls.append(deepcopy(query))
        for index, row in enumerate(self.rows):
            if all(row.get(key) == value for key, value in query.items()):
                self.rows.pop(index)
                return True
        return False


class Database:
    def __init__(self, rows):
        self.collections = {
            name: Collection(name, rows.get(name, []))
            for name in (
                "room_user_messages",
                "room_agent_messages",
                "migration_markers",
            )
        }

    def __getitem__(self, name):
        return self.collections[name]


@pytest.mark.asyncio
async def test_migration_dry_run_apply_and_repeat(capsys):
    database = Database(
        {
            "room_user_messages": [
                {
                    "_id": 1,
                    "room_id": "room-1",
                    "message_id": "u-date",
                    "message_created_at": datetime(1970, 1, 1, tzinfo=UTC),
                },
                {
                    "_id": 2,
                    "room_id": "room-1",
                    "message_id": "u-naive",
                    "message_created_at": datetime(1970, 1, 1),
                },
            ],
            "room_agent_messages": [
                {
                    "_id": 1,
                    "room_id": "room-1",
                    "message_id": "a-z",
                    "message_created_at": "1970-01-01T00:00:00Z",
                    "timeline_sort_us": 0,
                },
                {
                    "_id": 2,
                    "room_id": "room-1",
                    "message_id": "a-offset",
                    "message_created_at": "1970-01-01T08:00:00+08:00",
                },
            ],
        }
    )

    dry = await run_migration(database, apply=False, batch_size=1)
    assert dry["room_user_messages"].missing == 2
    assert all(
        "timeline_sort_us" not in row for row in database["room_user_messages"].rows
    )

    applied = await run_migration(database, apply=True, batch_size=1)
    assert applied["room_user_messages"].updated == 2
    assert applied["room_agent_messages"].updated == 1
    assert database["migration_markers"].rows == [
        {
            "_id": "room_timeline_sort_keys_v1",
            "marker_id": "room_timeline_sort_keys_v1",
            "version": 1,
            "status": "complete",
            "completed_at": database["migration_markers"].rows[0]["completed_at"],
            "collections": {
                "room_user_messages": {"scanned": 2, "correct": 2},
                "room_agent_messages": {"scanned": 2, "correct": 2},
            },
        }
    ]
    repeated = await run_migration(database, apply=True, batch_size=2)
    assert repeated["room_user_messages"].updated == 0
    assert repeated["room_agent_messages"].updated == 0
    markers = database["migration_markers"]
    assert markers.delete_one_calls == [
        {"_id": "room_timeline_sort_keys_v1"},
        {"_id": "room_timeline_sort_keys_v1"},
    ]
    assert all(
        query == {"_id": "room_timeline_sort_keys_v1"}
        for query, _update, _kwargs in markers.update_one_calls
    )
    output = capsys.readouterr().out
    assert "message_content" not in output


@pytest.mark.asyncio
async def test_migration_mixed_bson_ids_cross_batches_are_all_scanned():
    database = Database(
        {
            "room_user_messages": [
                {
                    "_id": 1,
                    "room_id": "room-1",
                    "message_id": "int-id",
                    "message_created_at": "1970-01-01T00:00:00Z",
                    "timeline_sort_us": 0,
                },
                {
                    "_id": "string-id",
                    "room_id": "room-1",
                    "message_id": "string-id",
                    "message_created_at": "1970-01-01T00:00:00Z",
                    "timeline_sort_us": 0,
                },
                {
                    "_id": ObjectId(),
                    "room_id": "room-1",
                    "message_id": "object-id-conflict",
                    "message_created_at": "1970-01-01T00:00:00Z",
                    "timeline_sort_us": 1,
                },
            ]
        }
    )

    stats = await audit_collection(database["room_user_messages"], batch_size=2)

    assert stats.scanned == 3
    assert stats.conflicts == 1
    assert database["migration_markers"].rows == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migration_real_mongo_mixed_bson_ids_cannot_write_marker():
    mongo_url = os.getenv("HYBRO_TIMELINE_TEST_MONGODB_URL")
    if not mongo_url:
        pytest.skip("set HYBRO_TIMELINE_TEST_MONGODB_URL for real Mongo regression")

    client = AsyncIOMotorClient(mongo_url)
    database_name = f"hybro_timeline_mixed_ids_{uuid.uuid4().hex}"
    database = client[database_name]
    try:
        await database.room_user_messages.insert_many(
            [
                {
                    "_id": 1,
                    "room_id": "room-1",
                    "message_id": "int-1",
                    "message_created_at": "1970-01-01T00:00:00Z",
                    "timeline_sort_us": 0,
                },
                {
                    "_id": 2,
                    "room_id": "room-1",
                    "message_id": "int-2",
                    "message_created_at": "1970-01-01T00:00:00Z",
                    "timeline_sort_us": 0,
                },
                {
                    "_id": ObjectId(),
                    "room_id": "room-1",
                    "message_id": "object-id-conflict",
                    "message_created_at": "1970-01-01T00:00:00Z",
                    "timeline_sort_us": 1,
                },
            ]
        )

        with pytest.raises(RuntimeError, match="audit failed"):
            await run_migration(database, apply=True, batch_size=2)

        assert await database.room_user_messages.count_documents({}) == 3
        assert await database.migration_markers.count_documents({}) == 0
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row,match",
    [
        (
            {
                "_id": 1,
                "room_id": "room-1",
                "message_id": "conflict",
                "message_created_at": "1970-01-01T00:00:00Z",
                "timeline_sort_us": 1,
            },
            "conflict",
        ),
        (
            {"_id": 1, "room_id": "room-1", "message_id": "missing"},
            "invalid_time",
        ),
        (
            {
                "_id": 1,
                "room_id": "room-1",
                "message_id": "invalid",
                "message_created_at": "bad",
            },
            "invalid_time",
        ),
    ],
)
async def test_migration_fails_without_writes_for_conflicts_or_invalid_time(
    row, match, capsys
):
    database = Database({"room_user_messages": [row]})

    with pytest.raises(RuntimeError, match="no updates were applied"):
        await run_migration(database, apply=True, batch_size=10)

    assert database["room_user_messages"].bulk_calls == 0
    assert match in capsys.readouterr().out


@pytest.mark.asyncio
async def test_failed_apply_invalidates_prior_completion_marker():
    database = Database(
        {
            "room_user_messages": [
                {
                    "_id": 1,
                    "room_id": "room-1",
                    "message_id": "conflict",
                    "message_created_at": "1970-01-01T00:00:00Z",
                    "timeline_sort_us": 1,
                }
            ],
            "migration_markers": [
                {
                    "_id": "room_timeline_sort_keys_v1",
                    "marker_id": "room_timeline_sort_keys_v1",
                    "version": 1,
                    "status": "complete",
                }
            ],
        }
    )

    with pytest.raises(RuntimeError, match="audit failed"):
        await run_migration(database, apply=True, batch_size=10)

    assert database["migration_markers"].rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "created_at",
    [
        datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        datetime(2026, 1, 2, 3, 4, 5, 987654),
    ],
)
async def test_migration_accepts_canonicalized_datetime_insert(created_at):
    inserted = normalize_timeline_document(
        {
            "_id": 1,
            "room_id": "room-1",
            "message_id": "datetime-message",
            "message_created_at": created_at,
        }
    )
    database = Database({"room_user_messages": [inserted]})

    results = await run_migration(database, apply=False, batch_size=10)

    assert results["room_user_messages"].correct == 1
    assert results["room_user_messages"].conflicts == 0


@pytest.mark.asyncio
async def test_migration_fails_when_timestamp_changes_after_audit():
    database = Database(
        {
            "room_user_messages": [
                {
                    "_id": 1,
                    "room_id": "room-1",
                    "message_id": "raced",
                    "message_created_at": "1970-01-01T00:00:00Z",
                }
            ]
        }
    )
    collection = database["room_user_messages"]
    original_bulk_write = collection.bulk_write

    async def raced_bulk_write(operations, ordered=True):
        collection.rows[0]["message_created_at"] = "1970-01-01T00:00:01Z"
        return await original_bulk_write(operations, ordered=ordered)

    collection.bulk_write = raced_bulk_write

    with pytest.raises(RuntimeError, match="migration write race"):
        await run_migration(database, apply=True, batch_size=10)

    assert "timeline_sort_us" not in collection.rows[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        {
            "_id": 1,
            "room_id": "",
            "message_id": "message-1",
            "message_created_at": "1970-01-01T00:00:00Z",
        },
        {
            "_id": 1,
            "room_id": "room-1",
            "message_id": None,
            "message_created_at": "1970-01-01T00:00:00Z",
        },
    ],
)
async def test_migration_reports_invalid_identity_for_manual_repair(row, capsys):
    database = Database({"room_user_messages": [row]})

    with pytest.raises(RuntimeError, match="manual data repair"):
        await run_migration(database, apply=True, batch_size=10)

    assert database["room_user_messages"].bulk_calls == 0
    assert database["migration_markers"].rows == []
    output = capsys.readouterr().out
    assert "invalid_identity=1" in output
    assert "message_content" not in output


@pytest.mark.asyncio
async def test_migration_apply_revalidates_existing_key_added_after_initial_audit():
    database = Database(
        {
            "room_user_messages": [
                {
                    "_id": 1,
                    "room_id": "room-1",
                    "message_id": "raced-before-apply",
                    "message_created_at": "1970-01-01T00:00:00Z",
                }
            ]
        }
    )
    collection = database["room_user_messages"]
    original_find = collection.find
    find_calls = 0

    def raced_find(query, projection=None):
        nonlocal find_calls
        find_calls += 1
        if find_calls == 2:
            collection.rows[0]["timeline_sort_us"] = 1
        return original_find(query, projection=projection)

    collection.find = raced_find

    with pytest.raises(RuntimeError, match="apply encountered conflict"):
        await run_migration(database, apply=True, batch_size=10)

    assert collection.bulk_calls == 0
    assert database["migration_markers"].rows == []


@pytest.mark.asyncio
async def test_migration_final_audit_rejects_concurrently_populated_conflicting_key():
    database = Database(
        {
            "room_user_messages": [
                {
                    "_id": 1,
                    "room_id": "room-1",
                    "message_id": "raced-key",
                    "message_created_at": "1970-01-01T00:00:00Z",
                }
            ]
        }
    )
    collection = database["room_user_messages"]
    original_bulk_write = collection.bulk_write

    async def raced_bulk_write(operations, ordered=True):
        result = await original_bulk_write(operations, ordered=ordered)
        collection.rows[0]["timeline_sort_us"] = 1
        return result

    collection.bulk_write = raced_bulk_write

    with pytest.raises(RuntimeError, match="final audit failed"):
        await run_migration(database, apply=True, batch_size=10)

    assert database["migration_markers"].rows == []


def test_migration_module_cli_help_smoke():
    backend_dir = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.migrate_room_timeline_sort_keys",
            "--help",
        ],
        cwd=backend_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--apply" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr
