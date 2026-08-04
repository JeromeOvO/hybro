#!/usr/bin/env python3
"""Backfill private room-message timeline keys. Defaults to a read-only audit."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

from common.config import settings
from room.timeline import (
    TIMELINE_MIGRATION_MARKER_COLLECTION,
    TIMELINE_MIGRATION_MARKER_ID,
    TIMELINE_MIGRATION_VERSION,
    timeline_sort_us_from_value,
)

COLLECTIONS = ("room_user_messages", "room_agent_messages")
SAMPLE_LIMIT = 5


@dataclass
class MigrationStats:
    scanned: int = 0
    correct: int = 0
    missing: int = 0
    updated: int = 0
    invalid_time: int = 0
    conflicts: int = 0
    invalid_identity: int = 0

    @property
    def failed(self) -> bool:
        return self.invalid_time > 0 or self.conflicts > 0 or self.invalid_identity > 0

    @property
    def complete(self) -> bool:
        return not self.failed and self.missing == 0


async def _pages(collection: Any, batch_size: int):
    # Consume one server cursor from beginning to end. Re-querying with
    # ``_id > last_id`` can skip documents when a collection contains mixed BSON
    # _id types because Mongo comparison operators apply type bracketing.
    cursor = collection.find(
        {},
        projection={
            "_id": 1,
            "room_id": 1,
            "message_id": 1,
            "message_created_at": 1,
            "timeline_sort_us": 1,
        },
    ).sort([("_id", 1)])
    while rows := await cursor.to_list(length=batch_size):
        yield rows


def _valid_identity(row: dict[str, Any]) -> bool:
    return all(
        isinstance(row.get(field), str) and bool(row[field].strip())
        for field in ("room_id", "message_id")
    )


def _print_sample(collection: Any, row: dict[str, Any], issue: str) -> None:
    print(f"collection={collection.name} message_id={row.get('message_id')} {issue}=1")


def _record_sample(
    collection: Any,
    row: dict[str, Any],
    issue: str,
    sample_counts: dict[str, int],
) -> None:
    if sample_counts[issue] >= SAMPLE_LIMIT:
        return
    _print_sample(collection, row, issue)
    sample_counts[issue] += 1


async def audit_collection(collection: Any, *, batch_size: int) -> MigrationStats:
    stats = MigrationStats()
    sample_counts = {
        "invalid_identity": 0,
        "invalid_time": 0,
        "missing_key": 0,
        "conflict": 0,
    }
    async for rows in _pages(collection, batch_size):
        for row in rows:
            stats.scanned += 1
            if not _valid_identity(row):
                stats.invalid_identity += 1
                _record_sample(collection, row, "invalid_identity", sample_counts)
                continue
            try:
                computed = timeline_sort_us_from_value(row.get("message_created_at"))
            except ValueError:
                stats.invalid_time += 1
                _record_sample(collection, row, "invalid_time", sample_counts)
                continue
            if "timeline_sort_us" not in row:
                stats.missing += 1
                _record_sample(collection, row, "missing_key", sample_counts)
                continue
            existing = row.get("timeline_sort_us")
            if (
                isinstance(existing, bool)
                or not isinstance(existing, int)
                or existing != computed
            ):
                stats.conflicts += 1
                _record_sample(collection, row, "conflict", sample_counts)
            else:
                stats.correct += 1
    return stats


def _validate_apply_row(collection: Any, row: dict[str, Any]) -> int | None:
    if not _valid_identity(row):
        raise RuntimeError(
            f"collection={collection.name} migration apply encountered invalid "
            f"identity message_id={row.get('message_id')}; manual repair required"
        )
    try:
        computed = timeline_sort_us_from_value(row.get("message_created_at"))
    except ValueError as exc:
        raise RuntimeError(
            f"collection={collection.name} migration apply encountered invalid time "
            f"message_id={row.get('message_id')}"
        ) from exc
    if "timeline_sort_us" in row:
        existing = row.get("timeline_sort_us")
        if (
            isinstance(existing, bool)
            or not isinstance(existing, int)
            or existing != computed
        ):
            raise RuntimeError(
                f"collection={collection.name} migration apply encountered conflict "
                f"message_id={row.get('message_id')}"
            )
        return None
    return computed


async def apply_collection(
    collection: Any,
    *,
    batch_size: int,
    stats: MigrationStats,
) -> None:
    async for rows in _pages(collection, batch_size):
        operations = []
        for row in rows:
            computed = _validate_apply_row(collection, row)
            if computed is None:
                continue
            operations.append(
                UpdateOne(
                    {
                        "_id": row["_id"],
                        "room_id": row.get("room_id"),
                        "message_id": row.get("message_id"),
                        "message_created_at": row.get("message_created_at"),
                        "timeline_sort_us": {"$exists": False},
                    },
                    {"$set": {"timeline_sort_us": computed}},
                )
            )
        if operations:
            result = await collection.bulk_write(operations, ordered=True)
            planned = len(operations)
            if result.modified_count != planned:
                raise RuntimeError(
                    f"collection={collection.name} migration write race: "
                    f"planned={planned} modified={result.modified_count}"
                )
            stats.updated += result.modified_count


def _print_summary(name: str, stats: MigrationStats, *, phase: str) -> None:
    print(
        f"collection={name} phase={phase} scanned={stats.scanned} "
        f"correct={stats.correct} missing={stats.missing} "
        f"invalid_identity={stats.invalid_identity} "
        f"invalid_time={stats.invalid_time} conflicts={stats.conflicts}"
    )


async def _write_completion_marker(
    database: Any,
    results: dict[str, MigrationStats],
) -> None:
    collections = {
        name: {"scanned": stats.scanned, "correct": stats.correct}
        for name, stats in results.items()
    }
    await database[TIMELINE_MIGRATION_MARKER_COLLECTION].update_one(
        {"_id": TIMELINE_MIGRATION_MARKER_ID},
        {
            "$set": {
                "marker_id": TIMELINE_MIGRATION_MARKER_ID,
                "version": TIMELINE_MIGRATION_VERSION,
                "status": "complete",
                "completed_at": datetime.now(UTC),
                "collections": collections,
            }
        },
        upsert=True,
    )


async def run_migration(
    database: Any,
    *,
    apply: bool,
    batch_size: int,
) -> dict[str, MigrationStats]:
    if apply:
        # Invalidate prior evidence before touching message rows. Any failure below
        # must leave startup unable to rely on a stale successful audit marker.
        await database[TIMELINE_MIGRATION_MARKER_COLLECTION].delete_one(
            {"_id": TIMELINE_MIGRATION_MARKER_ID}
        )
    initial: dict[str, MigrationStats] = {}
    for name in COLLECTIONS:
        stats = await audit_collection(database[name], batch_size=batch_size)
        initial[name] = stats
        _print_summary(name, stats, phase="initial")
    if any(stats.failed for stats in initial.values()):
        if any(stats.invalid_identity for stats in initial.values()):
            raise RuntimeError(
                "timeline migration audit found invalid room/message identity; "
                "manual data repair is required and no updates were applied"
            )
        raise RuntimeError("timeline migration audit failed; no updates were applied")
    if not apply:
        print("dry_run=1 updated=0 marker_written=0")
        return initial

    for name in COLLECTIONS:
        await apply_collection(
            database[name], batch_size=batch_size, stats=initial[name]
        )
        print(f"collection={name} updated={initial[name].updated}")

    final: dict[str, MigrationStats] = {}
    for name in COLLECTIONS:
        stats = await audit_collection(database[name], batch_size=batch_size)
        stats.updated = initial[name].updated
        final[name] = stats
        _print_summary(name, stats, phase="final")
    if any(not stats.complete for stats in final.values()):
        raise RuntimeError(
            "timeline migration final audit failed; completion marker was not written"
        )

    await _write_completion_marker(database, final)
    print(
        f"marker_id={TIMELINE_MIGRATION_MARKER_ID} "
        f"version={TIMELINE_MIGRATION_VERSION} status=complete"
    )
    return final


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--mongo-url", default=settings.mongodb_url)
    parser.add_argument("--database", default=settings.mongodb_db_name)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    return args


async def _main() -> None:
    args = _parse_args()
    client = AsyncIOMotorClient(args.mongo_url)
    try:
        await client.admin.command("ping")
        await run_migration(
            client[args.database], apply=args.apply, batch_size=args.batch_size
        )
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(_main())
