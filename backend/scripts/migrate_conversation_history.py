#!/usr/bin/env python3
"""Cut room-memory history over to the top-level canonical field.

The command is a read-only audit unless ``--apply`` is supplied. It never drops an
unreadable history item: malformed documents block the entire apply pass.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from common.config import settings
from context_memory.compaction import brief_summary_from_content
from context_memory.content_storage import is_content_expired

COLLECTION_NAME = "room_memories"
CONTENT_COLLECTION_NAME = "conversation_content"
SAMPLE_LIMIT = 10
_MISSING = object()


class MigrationBlocker(ValueError):
    """A document cannot be reconciled losslessly."""


class MissingCompactContentBlocker(MigrationBlocker):
    """A compact turn has no trustworthy source for its brief summary."""


@dataclass(slots=True)
class DocumentPlan:
    document_id: Any
    category: str
    history: list[dict[str, Any]]
    needs_update: bool
    direct_snapshot: Any
    nested_snapshot: Any
    backfill_count: int = 0


@dataclass(slots=True)
class MigrationStats:
    scanned: int = 0
    direct_only: int = 0
    legacy_only: int = 0
    equal: int = 0
    divergent: int = 0
    empty: int = 0
    would_update: int = 0
    updated: int = 0
    backfill_count: int = 0
    backfilled: int = 0
    blockers: int = 0
    missing_content_blockers: int = 0

    def record(self, plan: DocumentPlan) -> None:
        self.scanned += 1
        setattr(self, plan.category, getattr(self, plan.category) + 1)
        if plan.needs_update:
            self.would_update += 1
        self.backfill_count += plan.backfill_count


def _history_field(value: Any, *, path: str) -> list[dict[str, Any]] | None:
    if value is _MISSING:
        return None
    if not isinstance(value, list):
        raise MigrationBlocker(f"{path} must be an array when present")
    history: list[dict[str, Any]] = []
    for index, turn in enumerate(value):
        if not isinstance(turn, dict):
            raise MigrationBlocker(f"{path}[{index}] must be an object")
        history.append(deepcopy(turn))
    return history


def reconcile_histories(
    nested: list[dict[str, Any]] | None,
    direct: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge nested first and direct second, with direct winning on turn_id.

    Items without a ``turn_id`` (or with a BSON null id) are never deduplicated and
    retain their stable source order. The replacement position for an id remains
    the position of its first occurrence, matching the former runtime reducer.
    """

    reconciled: list[dict[str, Any]] = []
    identities: list[tuple[Any, int]] = []
    for history in (nested or [], direct or []):
        for turn in history:
            candidate = deepcopy(turn)
            turn_id = candidate.get("turn_id")
            if turn_id is None:
                reconciled.append(candidate)
                continue
            existing_index = next(
                (index for identity, index in identities if identity == turn_id), None
            )
            if existing_index is None:
                identities.append((deepcopy(turn_id), len(reconciled)))
                reconciled.append(candidate)
            else:
                reconciled[existing_index] = candidate
    return reconciled


def plan_document(row: dict[str, Any]) -> DocumentPlan:
    if "_id" not in row:
        raise MigrationBlocker("document is missing _id")
    memory_content = row.get("memory_content", _MISSING)
    if memory_content is _MISSING or memory_content is None:
        nested_raw = _MISSING
    elif isinstance(memory_content, dict):
        nested_raw = memory_content.get("conversation_history", _MISSING)
    else:
        raise MigrationBlocker("memory_content must be an object or null")

    direct_raw = row.get("conversation_history", _MISSING)
    nested = _history_field(nested_raw, path="memory_content.conversation_history")
    direct = _history_field(direct_raw, path="conversation_history")
    reconciled = reconcile_histories(nested, direct)

    if direct is not None and nested is None:
        category = "direct_only"
        needs_update = False
    elif direct is None and nested is not None:
        category = "legacy_only"
        needs_update = True
    elif direct is None and nested is None:
        category = "empty"
        needs_update = True
    elif direct == nested:
        category = "equal"
        needs_update = True
    else:
        category = "divergent"
        needs_update = True

    return DocumentPlan(
        document_id=row["_id"],
        category=category,
        history=reconciled,
        needs_update=needs_update,
        direct_snapshot=direct_raw,
        nested_snapshot=nested_raw,
    )


def _nonempty_string(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _one_liner_fallback(turn: dict[str, Any]) -> str | None:
    notes = turn.get("turn_notes")
    if not isinstance(notes, dict):
        return None
    one_liner = _nonempty_string(notes.get("one_liner"))
    if one_liner is None or " ".join(one_liner.split()).casefold() == "[compact turn]":
        return None
    return one_liner


async def _content_for_compact_turn(
    content_collection: Any,
    *,
    room_id: Any,
    turn: dict[str, Any],
) -> str | None:
    content_ref = turn.get("content_ref")
    document_id = (
        content_ref.get("document_id") if isinstance(content_ref, dict) else None
    )
    document = None
    if document_id is not None:
        document = await content_collection.find_one({"document_id": document_id})
        if document is None:
            document = await content_collection.find_one({"_id": document_id})

    turn_id = turn.get("turn_id")
    if (
        (document is None or is_content_expired(document))
        and room_id is not None
        and turn_id is not None
    ):
        document = await content_collection.find_one(
            {"room_id": room_id, "turn_id": turn_id}
        )

    if document is None or is_content_expired(document):
        return None
    return _nonempty_string(document.get("content"))


async def plan_document_with_backfill(
    row: dict[str, Any], content_collection: Any
) -> DocumentPlan:
    """Reconcile a document, then recover missing compact-turn summaries."""
    plan = plan_document(row)
    room_id = row.get("room_id")
    for index, turn in enumerate(plan.history):
        if turn.get("representation") != "compact":
            continue
        if _nonempty_string(turn.get("brief_summary")) is not None:
            continue

        source = await _content_for_compact_turn(
            content_collection,
            room_id=room_id,
            turn=turn,
        )
        if source is None:
            source = _one_liner_fallback(turn)
        if source is None:
            raise MissingCompactContentBlocker(
                "compact turn is missing full content and a reliable one_liner; "
                f"history_index={index} turn_id={turn.get('turn_id')}"
            )

        turn["brief_summary"] = brief_summary_from_content(source)
        plan.backfill_count += 1

    if plan.backfill_count:
        plan.needs_update = True
    return plan


async def _pages(collection: Any, batch_size: int):
    cursor = collection.find(
        {},
        projection={
            "_id": 1,
            "room_id": 1,
            "memory_content": 1,
            "conversation_history": 1,
        },
    ).sort([("_id", 1)])
    while rows := await cursor.to_list(length=batch_size):
        yield rows


async def audit_collection(
    collection: Any,
    content_collection: Any,
    *,
    batch_size: int,
) -> MigrationStats:
    stats = MigrationStats()
    samples = 0
    async for rows in _pages(collection, batch_size):
        for row in rows:
            try:
                plan = await plan_document_with_backfill(row, content_collection)
                stats.record(plan)
            except MigrationBlocker as exc:
                stats.scanned += 1
                stats.blockers += 1
                if isinstance(exc, MissingCompactContentBlocker):
                    stats.missing_content_blockers += 1
                if samples < SAMPLE_LIMIT:
                    print(f"document_id={row.get('_id')} blocker={exc}")
                    samples += 1
    return stats


def _snapshot_query(plan: DocumentPlan) -> dict[str, Any]:
    query: dict[str, Any] = {"_id": plan.document_id}
    if plan.direct_snapshot is _MISSING:
        query["conversation_history"] = {"$exists": False}
    else:
        query["conversation_history"] = plan.direct_snapshot
    if plan.nested_snapshot is _MISSING:
        query["memory_content.conversation_history"] = {"$exists": False}
    else:
        query["memory_content.conversation_history"] = plan.nested_snapshot
    return query


async def apply_collection(
    collection: Any,
    content_collection: Any,
    *,
    batch_size: int,
) -> tuple[int, int]:
    updated = 0
    backfilled = 0
    async for rows in _pages(collection, batch_size):
        for row in rows:
            plan = await plan_document_with_backfill(row, content_collection)
            if not plan.needs_update:
                continue
            result = await collection.update_one(
                _snapshot_query(plan),
                {
                    "$set": {"conversation_history": plan.history},
                    "$unset": {"memory_content.conversation_history": ""},
                },
            )
            matched = getattr(result, "matched_count", None)
            if matched is None:
                matched = int(bool(result))
            if matched != 1:
                raise RuntimeError(
                    "conversation-history migration snapshot changed during apply; "
                    f"document_id={plan.document_id}. Stop traffic and rerun."
                )
            modified = getattr(result, "modified_count", None)
            updated += int(bool(result) if modified is None else modified)
            backfilled += plan.backfill_count
    return updated, backfilled


def _print_summary(stats: MigrationStats, *, phase: str) -> None:
    print(
        f"collection={COLLECTION_NAME} phase={phase} scanned={stats.scanned} "
        f"direct_only={stats.direct_only} legacy_only={stats.legacy_only} "
        f"equal={stats.equal} divergent={stats.divergent} empty={stats.empty} "
        f"would_update={stats.would_update} updated={stats.updated} "
        f"backfill_count={stats.backfill_count} backfilled={stats.backfilled} "
        f"blockers={stats.blockers} "
        f"missing_content_blockers={stats.missing_content_blockers}"
    )


async def run_migration(
    database: Any,
    *,
    apply: bool,
    batch_size: int,
) -> MigrationStats:
    collection = database[COLLECTION_NAME]
    content_collection = database[CONTENT_COLLECTION_NAME]
    initial = await audit_collection(
        collection,
        content_collection,
        batch_size=batch_size,
    )
    _print_summary(initial, phase="initial")
    if initial.blockers:
        raise RuntimeError(
            "conversation-history audit found blockers; no updates were applied"
        )
    if not apply:
        print("dry_run=1 updated=0")
        return initial

    initial.updated, initial.backfilled = await apply_collection(
        collection,
        content_collection,
        batch_size=batch_size,
    )
    print(f"apply=1 updated={initial.updated} backfilled={initial.backfilled}")
    final = await audit_collection(
        collection,
        content_collection,
        batch_size=batch_size,
    )
    final.updated = initial.updated
    final.backfilled = initial.backfilled
    _print_summary(final, phase="final")
    if final.blockers or final.would_update:
        raise RuntimeError(
            "conversation-history final audit failed; rerun only after investigating"
        )
    return final


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit or apply the canonical conversation-history cutover"
    )
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
