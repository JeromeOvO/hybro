"""
Migration: Backfill client_request_id for legacy room user messages.

Target rows:
  - room_user_messages documents where message_type == "user"
  - and client_request_id is missing, null, or an empty string

Operation:
  - set client_request_id = message_id (idempotent)

Run with:
    python -m database.migration.backfill_user_message_client_request_id --dry-run
    python -m database.migration.backfill_user_message_client_request_id
"""

import argparse
import asyncio

from common.utils.logger import get_logger
from database.mongodb import mongodb

logger = get_logger(__name__)


def _missing_client_request_filter() -> dict:
    return {
        "message_type": "user",
        "$or": [
            {"client_request_id": {"$exists": False}},
            {"client_request_id": None},
            {"client_request_id": ""},
        ],
    }


async def run_backfill(dry_run: bool = False) -> int:
    await mongodb.connect()
    if not mongodb.client:
        logger.error("MongoDB connection failed, aborting migration.")
        return 1

    try:
        coll = mongodb.room_user_messages_collection
        missing_filter = _missing_client_request_filter()

        before_count = await coll.count_documents(missing_filter)
        logger.info("Missing client_request_id before migration: %d", before_count)

        if dry_run:
            logger.info("[DRY RUN] No documents were updated.")
            return 0

        result = await coll.update_many(
            missing_filter,
            [
                {
                    "$set": {
                        "client_request_id": "$message_id",
                    }
                }
            ],
        )
        logger.info("Matched documents: %d", result.matched_count)
        logger.info("Modified documents: %d", result.modified_count)

        # Enforce schema-level invariant for turn roots after backfill.
        # Partial index keeps non-user documents out of the constraint.
        await coll.create_index(
            "client_request_id",
            name="idx_user_client_request_id_required",
            partialFilterExpression={
                "message_type": "user",
                    # Keep partial filter compatible with older MongoDB engines
                    # that reject $ne in partial index expressions.
                    "client_request_id": {"$type": "string"},
            },
        )

        after_count = await coll.count_documents(missing_filter)
        logger.info("Missing client_request_id after migration: %d", after_count)

        if after_count != 0:
            logger.error("Verification failed: missing client_request_id rows remain.")
            return 2

        logger.info("Migration verification passed.")
        return 0
    finally:
        await mongodb.close_database_connection()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_backfill(dry_run=args.dry_run)))
