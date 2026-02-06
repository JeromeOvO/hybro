"""
Migration: Fix legacy stale tasks by marking them as failed

This migration marks legacy non-terminal tasks as failed so they display
correctly in the frontend instead of showing "Task timed out".

Background:
- Legacy messages may have non-terminal task status (submitted, working, input-required)
- These tasks will never complete because they're abandoned
- The frontend shows them as "Task timed out" which is confusing
- This migration marks them as properly failed with a clear error message

This migration:
1. Finds all agent messages with non-terminal task status older than a threshold
2. Updates their task status to "failed" with an appropriate error message
3. Also backfills task_updated_at if missing

Run with:
    python -m database.migration.fix_legacy_stale_tasks --dry-run  # Preview changes
    python -m database.migration.fix_legacy_stale_tasks            # Apply changes
"""

import asyncio
import sys
from datetime import timedelta

from common.utils.logger import get_logger
from common.utils.time import utcnow
from database.mongodb import get_db, mongodb

logger = get_logger(__name__)

# Non-terminal states that indicate abandoned tasks
NON_TERMINAL_STATES = ["submitted", "working", "input-required", "auth-required"]

# Tasks older than this are considered abandoned
STALE_THRESHOLD_HOURS = 1


async def fix_legacy_stale_tasks(dry_run: bool = False):
    """
    Fix legacy stale tasks by marking them as failed.
    """
    mongo_db = await get_db()
    collection = mongo_db.room_agent_messages

    threshold = utcnow() - timedelta(hours=STALE_THRESHOLD_HOURS)
    # Convert to ISO string for comparison (handles both string and datetime fields)
    threshold_str = threshold.isoformat()

    # Step 1: Count affected messages
    logger.info("Step 1: Counting legacy stale tasks...")
    logger.info(f"Looking for tasks older than {STALE_THRESHOLD_HOURS} hour(s) with non-terminal status")
    logger.info(f"Threshold: {threshold_str}")
    
    # Find messages with non-terminal task status that are old
    # Use $lt on string comparison (ISO format strings compare correctly)
    query = {
        "message_content.message_task.status.state": {"$in": NON_TERMINAL_STATES},
        "message_created_at": {"$lt": threshold_str},
    }
    
    affected_count = await collection.count_documents(query)
    logger.info(f"Found {affected_count} stale tasks to fix")

    if affected_count == 0:
        logger.info("No stale tasks found. Migration complete.")
        return

    # Step 2: Show status distribution
    logger.info("\nStep 2: Status distribution of affected tasks...")
    pipeline = [
        {"$match": query},
        {"$group": {
            "_id": "$message_content.message_task.status.state",
            "count": {"$sum": 1}
        }},
        {"$sort": {"count": -1}}
    ]
    
    cursor = collection.aggregate(pipeline)
    async for doc in cursor:
        logger.info(f"  {doc['_id']}: {doc['count']}")

    # Step 3: Preview some affected messages
    logger.info("\nStep 3: Previewing affected messages...")
    cursor = collection.find(query).limit(10)
    
    preview_count = 0
    async for doc in cursor:
        preview_count += 1
        message_id = doc.get("message_id", "unknown")
        agent_id = doc.get("agent_id", "unknown")
        created_at = doc.get("message_created_at", "unknown")
        task_status = doc.get("message_content", {}).get("message_task", {}).get("status", {}).get("state", "unknown")
        
        logger.info(
            f"  [{preview_count}] message_id={message_id}, agent_id={agent_id}, "
            f"created_at={created_at}, task_status={task_status}"
        )
    
    if affected_count > 10:
        logger.info(f"  ... and {affected_count - 10} more messages")

    if dry_run:
        logger.info(
            f"\n[DRY RUN] Would update {affected_count} messages. "
            "Run without --dry-run to apply changes."
        )
        return

    # Step 4: Update messages
    logger.info(f"\nStep 4: Updating {affected_count} stale tasks to failed status...")
    
    now = utcnow()
    error_message = "Task was abandoned and did not complete. This may have been caused by an agent error or system restart."
    
    # Update task status to failed and set task_updated_at
    result = await collection.update_many(
        query,
        [
            {
                "$set": {
                    "message_content.message_task.status.state": "failed",
                    "message_content.message_task.status.timestamp": now.isoformat(),
                    "message_content.message_task.status.message": {
                        "message_id": {"$concat": ["migration-", {"$toString": "$_id"}]},
                        "role": "agent",
                        "parts": [{"kind": "text", "text": error_message}],
                    },
                    "task_updated_at": {
                        "$ifNull": ["$task_updated_at", "$message_created_at"]
                    },
                }
            }
        ],
    )
    
    logger.info(f"Updated {result.modified_count} messages")

    # Step 5: Verify the update
    logger.info("\nStep 5: Verifying update...")
    remaining = await collection.count_documents(query)
    
    if remaining == 0:
        logger.info("Verification passed: All stale tasks have been marked as failed")
    else:
        logger.warning(f"Verification warning: {remaining} tasks still have non-terminal status")

    # Step 6: Show new status distribution
    logger.info("\nStep 6: New status distribution...")
    pipeline = [
        {"$match": {"message_content.message_task": {"$exists": True, "$ne": None}}},
        {"$group": {
            "_id": "$message_content.message_task.status.state",
            "count": {"$sum": 1}
        }},
        {"$sort": {"count": -1}}
    ]
    
    cursor = collection.aggregate(pipeline)
    async for doc in cursor:
        logger.info(f"  {doc['_id']}: {doc['count']}")

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("MIGRATION COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Tasks marked as failed: {result.modified_count}")


async def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        logger.info("Running in DRY RUN mode - no changes will be made")
    
    # Connect to MongoDB first
    await mongodb.connect()

    try:
        await fix_legacy_stale_tasks(dry_run=dry_run)
    finally:
        await mongodb.close_database_connection()


if __name__ == "__main__":
    asyncio.run(main())
