#!/usr/bin/env python3
"""One-shot repair: set rooms.processing_message_id to null where there are no non-terminal runs.

Same predicate as compaction skip-set and stale_task_checker legacy cleanup (runs-only busy).
Safe to re-run: only updates rooms that still have a non-null legacy field while idle on runs.

Usage (repo root = multi-agents-backend):

  export MONGODB_URL=...
  source .venv/bin/activate
  DRY_RUN=1 python database/migration/null_legacy_room_processing_message_id.py   # count only
  python database/migration/null_legacy_room_processing_message_id.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    from database.mongodb import mongodb

    await mongodb.connect()
    if not mongodb.client:
        print("ERROR: MongoDB not connected (check MONGODB_URL)", file=sys.stderr)
        return 1
    dry = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
    try:
        busy_ids = await mongodb.get_room_ids_with_non_terminal_runs()
        busy = list({rid for rid in busy_ids if rid})
        flt: dict = {"processing_message_id": {"$ne": None}}
        if busy:
            flt["room_id"] = {"$nin": busy}
        n = await mongodb.rooms_collection.count_documents(flt)
        print(f"rooms matching predicate (will null processing_message_id): {n}")
        if dry:
            print("DRY_RUN set — no writes")
            return 0
        res = await mongodb.rooms_collection.update_many(
            flt, {"$set": {"processing_message_id": None}}
        )
        print(f"modified_count={res.modified_count}")
    finally:
        await mongodb.close_database_connection()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
