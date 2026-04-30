#!/usr/bin/env python3
"""Ensure MongoDB indexes for `runs` and `run_events` (idempotent).

Usage (repo root = multi-agents-backend):

  export MONGODB_URL=...
  source .venv/bin/activate
  python scripts/migrations/run_run_lifecycle_indexes.py
"""

from __future__ import annotations

import asyncio
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
    try:
        await mongodb.create_run_lifecycle_indexes()
    finally:
        await mongodb.close_database_connection()
    print("OK: create_run_lifecycle_indexes finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
