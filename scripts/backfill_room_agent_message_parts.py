#!/usr/bin/env python3
"""
Backfill malformed A2A parts in room_agent_messages.message_content.message_task.

This script removes malformed parts (for example {"kind": "text"} without text)
from three task locations:
  - artifacts[*].parts
  - history[*].parts
  - status.message.parts

Default mode is dry-run. Pass --apply to persist changes.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
from dataclasses import dataclass
from datetime import UTC, datetime

from pymongo.errors import PyMongoError

from common.utils.a2a_helpers import sanitize_artifact_parts
from common.utils.logger import get_logger
from database.mongodb import MongoDB

logger = get_logger(__name__)


@dataclass
class BackfillStats:
    scanned: int = 0
    changed_docs: int = 0
    malformed_parts_removed: int = 0
    write_errors: int = 0


def _sanitize_task_parts(task: dict) -> tuple[dict, int]:
    """Return sanitized task dict and count of stripped malformed parts."""
    sanitized = copy.deepcopy(task)
    removed = 0

    for artifact in sanitized.get("artifacts") or []:
        parts = artifact.get("parts")
        if isinstance(parts, list):
            clean = sanitize_artifact_parts(parts)
            removed += max(0, len(parts) - len(clean))
            artifact["parts"] = clean

    for msg in sanitized.get("history") or []:
        parts = msg.get("parts")
        if isinstance(parts, list):
            clean = sanitize_artifact_parts(parts)
            removed += max(0, len(parts) - len(clean))
            msg["parts"] = clean

    status = sanitized.get("status") or {}
    status_message = status.get("message") or {}
    status_parts = status_message.get("parts")
    if isinstance(status_parts, list):
        clean = sanitize_artifact_parts(status_parts)
        removed += max(0, len(status_parts) - len(clean))
        status_message["parts"] = clean

    return sanitized, removed


async def _run(apply: bool, limit: int | None) -> int:
    mongo = MongoDB()
    await mongo.connect()

    stats = BackfillStats()
    try:
        query = {"message_content.message_task": {"$type": "object"}}
        projection = {"message_id": 1, "message_content.message_task": 1}
        cursor = mongo.room_agent_messages_collection.find(query, projection)
        if limit is not None and limit > 0:
            cursor = cursor.limit(limit)

        async for doc in cursor:
            stats.scanned += 1
            message_id = doc.get("message_id")
            task = ((doc.get("message_content") or {}).get("message_task") or {})
            if not isinstance(task, dict):
                continue

            sanitized_task, removed = _sanitize_task_parts(task)
            if removed <= 0:
                continue

            stats.changed_docs += 1
            stats.malformed_parts_removed += removed

            logger.info(
                "would_fix message_id=%s removed_parts=%d",
                message_id,
                removed,
            )

            if not apply:
                continue

            try:
                result = await mongo.room_agent_messages_collection.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "message_content.message_task": sanitized_task,
                            "task_updated_at": datetime.now(UTC),
                        }
                    },
                )
                if result.modified_count != 1:
                    logger.warning(
                        "update_not_applied message_id=%s matched=%d modified=%d",
                        message_id,
                        result.matched_count,
                        result.modified_count,
                    )
            except PyMongoError:
                stats.write_errors += 1
                logger.exception("failed_update message_id=%s", message_id)

    finally:
        await mongo.close_database_connection()

    print("\nBackfill summary")
    print(f"  scanned_docs:              {stats.scanned}")
    print(f"  docs_with_malformed_parts: {stats.changed_docs}")
    print(f"  malformed_parts_removed:   {stats.malformed_parts_removed}")
    print(f"  mode:                      {'apply' if apply else 'dry-run'}")
    print(f"  write_errors:              {stats.write_errors}")

    if stats.write_errors > 0:
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill malformed A2A parts in "
            "room_agent_messages.message_content.message_task"
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Persist updates to MongoDB (default: dry-run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of documents to scan",
    )
    args = parser.parse_args()
    return asyncio.run(_run(apply=args.apply, limit=args.limit))


if __name__ == "__main__":
    raise SystemExit(main())

