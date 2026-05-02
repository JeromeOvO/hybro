"""
Migration: Ensure MongoDB indexes for `runs` and `run_events` (idempotent).

Run this before or during deploy when the release notes call for it.
`create_index` is a no-op if the index already exists with the same options.

When: Any release that adds or changes run lifecycle indexes
      (see database/mongodb.py → create_run_lifecycle_indexes).

Run this script once:
    python -m database.migration.add_run_lifecycle_indexes

Or directly (from multi-agents-backend repo root, venv active, MONGODB_URL set):
    python database/migration/add_run_lifecycle_indexes.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

from database.mongodb import mongodb


async def create_run_lifecycle_indexes() -> int:
    await mongodb.connect()
    if not mongodb.client:
        print("ERROR: MongoDB not connected (check MONGODB_URL)", file=sys.stderr)
        return 1
    try:
        await mongodb.create_run_lifecycle_indexes()
    finally:
        await mongodb.close_database_connection()
    print("OK: create_run_lifecycle_indexes finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(create_run_lifecycle_indexes()))
