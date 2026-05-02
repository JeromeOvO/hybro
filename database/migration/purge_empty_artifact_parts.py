#!/usr/bin/env python3
"""One-shot repair: remove empty-dict ({}) entries from artifact parts arrays.

Root cause: protobuf Part(text="") serialises to {} via MessageToDict when
the oneof content variant holds a default value.  Earlier code paths let these
slip through to MongoDB, causing Pydantic validation failures on every read.

Safe to re-run: $pull {}<empty> is a no-op when no matches exist.

Usage (repo root = multi-agents-backend):

  export MONGODB_URL=...
  source .venv/bin/activate
  DRY_RUN=1 python database/migration/purge_empty_artifact_parts.py   # count only
  python database/migration/purge_empty_artifact_parts.py
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
        flt = {"message_content.message_task.artifacts.parts": {}}
        n = await mongodb.room_agent_messages_collection.count_documents(flt)
        print(f"room_agent_messages with empty-dict parts: {n}")
        if dry:
            print("DRY_RUN set — no writes")
            return 0
        if n == 0:
            print("Nothing to fix")
            return 0
        res = await mongodb.room_agent_messages_collection.update_many(
            flt,
            {"$pull": {"message_content.message_task.artifacts.$[].parts": {}}},
        )
        print(f"modified_count={res.modified_count}")
    finally:
        await mongodb.close_database_connection()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
