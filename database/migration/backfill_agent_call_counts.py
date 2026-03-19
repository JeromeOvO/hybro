"""
Migration: Recalculate call_count and call_success_count for all agents

Aggregates historical agent messages from room_agent_messages to compute
the actual call_count (total messages dispatched to each agent) and
call_success_count (messages that completed successfully).

Logic:
  - call_count          = total room_agent_messages per agent_id
  - call_success_count  = messages where the task reached "completed" state,
                          OR messages without task tracking (sync responses
                          that returned successfully without task status)

Run with:
    python -m database.migration.backfill_agent_call_counts --dry-run
    python -m database.migration.backfill_agent_call_counts
"""

import asyncio
import sys

from common.utils.logger import get_logger
from database.mongodb import mongodb

logger = get_logger(__name__)

FAILURE_STATES = {"failed", "canceled", "rejected"}


async def backfill_agent_call_counts(dry_run: bool = False):
    await mongodb.connect()
    if not mongodb.client:
        logger.error("MongoDB connection failed, aborting migration.")
        return

    try:
        messages_coll = mongodb.room_agent_messages_collection
        agents_coll = mongodb.agents_collection

        # Aggregate per agent_id: total messages and success count.
        # A message counts as "successful" if:
        #   - it has no task status at all (sync response that returned OK), or
        #   - its task reached "completed" state
        # A message counts as "not successful" only when the task state is
        # explicitly one of the failure states.
        pipeline = [
            {"$match": {"message_type": "agent", "agent_id": {"$ne": None}}},
            {
                "$group": {
                    "_id": "$agent_id",
                    "call_count": {"$sum": 1},
                    "call_success_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$in": [
                                        "$message_content.message_task.status.state",
                                        list(FAILURE_STATES),
                                    ]
                                },
                                0,
                                1,
                            ]
                        }
                    },
                }
            },
        ]

        logger.info("Aggregating call counts from room_agent_messages ...")
        counts: dict[str, dict[str, int]] = {}
        cursor = messages_coll.aggregate(pipeline)
        async for doc in cursor:
            counts[doc["_id"]] = {
                "call_count": doc["call_count"],
                "call_success_count": doc["call_success_count"],
            }

        logger.info("Found message history for %d agents.", len(counts))

        # Also ensure agents with zero messages get their fields set.
        all_agents_cursor = agents_coll.find({}, {"agent_id": 1})
        all_agent_ids: set[str] = set()
        async for agent in all_agents_cursor:
            aid = agent.get("agent_id")
            if aid:
                all_agent_ids.add(aid)

        for aid in all_agent_ids:
            if aid not in counts:
                counts[aid] = {"call_count": 0, "call_success_count": 0}

        logger.info("Total agents to update: %d", len(counts))

        # Preview
        for aid, vals in sorted(counts.items(), key=lambda x: -x[1]["call_count"])[:20]:
            logger.info(
                "  %-40s  call_count=%d  call_success_count=%d",
                aid,
                vals["call_count"],
                vals["call_success_count"],
            )
        if len(counts) > 20:
            logger.info("  ... and %d more agents", len(counts) - 20)

        if dry_run:
            total_calls = sum(v["call_count"] for v in counts.values())
            total_success = sum(v["call_success_count"] for v in counts.values())
            logger.info(
                "[DRY RUN] Would set call_count totalling %d and "
                "call_success_count totalling %d across %d agents. "
                "Run without --dry-run to apply.",
                total_calls,
                total_success,
                len(counts),
            )
            return

        # Apply
        updated = 0
        for aid, vals in counts.items():
            result = await agents_coll.update_one(
                {"agent_id": aid},
                {"$set": vals},
            )
            if result.modified_count:
                updated += 1

        logger.info("Updated %d / %d agents.", updated, len(counts))

        # Verify
        remaining = await agents_coll.count_documents(
            {
                "$or": [
                    {"call_count": {"$exists": False}},
                    {"call_success_count": {"$exists": False}},
                ]
            },
        )
        if remaining == 0:
            logger.info("Verification passed: all agents have call counter fields.")
        else:
            logger.warning("Verification: %d agents still missing fields.", remaining)

    finally:
        await mongodb.close_database_connection()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info("Running in DRY RUN mode — no changes will be made.")
    asyncio.run(backfill_agent_call_counts(dry_run=dry_run))
