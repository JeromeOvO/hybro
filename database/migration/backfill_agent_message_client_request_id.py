"""
Migration: Backfill client_request_id for legacy room agent messages.

Target rows:
  - room_agent_messages documents where message_type == "agent"
  - and client_request_id is missing, null, or an empty string

Resolution order:
  1) Traverse related_message_id chain to find a user message with client_request_id
  2) Fallback to turn_id user message lookup
  3) Fallback to message_id (idempotent safety net)

Run with:
    python -m database.migration.backfill_agent_message_client_request_id --dry-run
    python -m database.migration.backfill_agent_message_client_request_id
"""

import argparse
import asyncio
from typing import Any

from common.utils.logger import get_logger
from database.mongodb import mongodb

logger = get_logger(__name__)


def _missing_client_request_filter() -> dict[str, Any]:
    return {
        "message_type": "agent",
        "$or": [
            {"client_request_id": {"$exists": False}},
            {"client_request_id": None},
            {"client_request_id": ""},
        ],
    }


async def _resolve_from_chain(
    agent_doc: dict[str, Any],
    user_coll,
    agent_coll,
    max_hops: int = 12,
) -> str | None:
    visited: set[str] = set()
    cursor = agent_doc.get("related_message_id")
    for _ in range(max_hops):
        if not cursor or cursor in visited:
            break
        visited.add(cursor)

        user_doc = await user_coll.find_one(
            {"message_id": cursor},
            {"client_request_id": 1},
        )
        if user_doc and isinstance(user_doc.get("client_request_id"), str) and user_doc.get("client_request_id"):
            return user_doc["client_request_id"]

        parent_agent = await agent_coll.find_one(
            {"message_id": cursor},
            {"related_message_id": 1, "client_request_id": 1},
        )
        if not parent_agent:
            break
        parent_cid = parent_agent.get("client_request_id")
        if isinstance(parent_cid, str) and parent_cid:
            return parent_cid
        cursor = parent_agent.get("related_message_id")

    turn_id = agent_doc.get("turn_id")
    if turn_id:
        turn_user = await user_coll.find_one(
            {"message_id": turn_id},
            {"client_request_id": 1},
        )
        if (
            turn_user
            and isinstance(turn_user.get("client_request_id"), str)
            and turn_user.get("client_request_id")
        ):
            return turn_user["client_request_id"]
    return None


async def run_backfill(dry_run: bool = False) -> int:
    await mongodb.connect()
    if not mongodb.client:
        logger.error("MongoDB connection failed, aborting migration.")
        return 1

    try:
        agent_coll = mongodb.room_agent_messages_collection
        user_coll = mongodb.room_user_messages_collection
        missing_filter = _missing_client_request_filter()

        before_count = await agent_coll.count_documents(missing_filter)
        logger.info("Missing client_request_id before migration: %d", before_count)
        if before_count == 0:
            return 0

        if dry_run:
            logger.info("[DRY RUN] No documents were updated.")
            return 0

        modified = 0
        cursor = agent_coll.find(
            missing_filter,
            {"_id": 1, "message_id": 1, "related_message_id": 1, "turn_id": 1},
        )
        async for doc in cursor:
            resolved = await _resolve_from_chain(doc, user_coll, agent_coll)
            client_request_id = resolved or doc.get("message_id")
            if not client_request_id:
                continue
            result = await agent_coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {"client_request_id": client_request_id}},
            )
            modified += result.modified_count

        logger.info("Modified documents: %d", modified)

        await agent_coll.create_index(
            "client_request_id",
            name="idx_agent_client_request_id_present",
            partialFilterExpression={
                "message_type": "agent",
                "client_request_id": {"$type": "string"},
            },
        )

        after_count = await agent_coll.count_documents(missing_filter)
        logger.info("Missing client_request_id after migration: %d", after_count)
        if after_count != 0:
            logger.error("Verification failed: missing client_request_id rows remain.")
            return 2
        return 0
    finally:
        await mongodb.close_database_connection()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_backfill(dry_run=args.dry_run)))
