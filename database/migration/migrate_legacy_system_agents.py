import asyncio
import logging
import sys

from common.utils.logger import get_logger
from database.mongodb import get_db, mongodb

logger = get_logger(__name__)

async def run_migration():
    await mongodb.connect()
    try:
        db = await get_db()

        clarifier_legacy = ["supervisor_hitl", "supervisor_clarify"]
        hybro_legacy = [
            "supervisor_synthesis",
            "debate_summary",
            "non_debate_summary",
            "summary",
        ]

        total_agent_messages_migrated = 0
        total_memories_migrated = 0
        total_run_events_migrated = 0

        # 1. Update room_agent_messages
        logger.info("Migrating room_agent_messages...")

        # Clarifier
        result = await db.room_agent_messages.update_many(
            {"agent_id": {"$in": clarifier_legacy}},
            {"$set": {"agent_id": "system:clarifier"}},
        )
        total_agent_messages_migrated += result.modified_count
        logger.info(f"Updated {result.modified_count} clarifier agent messages.")

        # Hybro
        result = await db.room_agent_messages.update_many(
            {"agent_id": {"$in": hybro_legacy}},
            {"$set": {"agent_id": "system:hybro"}},
        )
        total_agent_messages_migrated += result.modified_count
        logger.info(f"Updated {result.modified_count} hybro agent messages.")

        # 2. Update room_memories
        logger.info("Migrating room_memories...")
        
        # Clarifier
        result = await db.room_memories.update_many(
            {"turns.agent_responses.agent_id": {"$in": clarifier_legacy}},
            {"$set": {"turns.$[].agent_responses.$[response].agent_id": "system:clarifier"}},
            array_filters=[{"response.agent_id": {"$in": clarifier_legacy}}]
        )
        total_memories_migrated += result.modified_count
        logger.info(f"Updated {result.modified_count} clarifier memories.")

        # Hybro
        result = await db.room_memories.update_many(
            {"turns.agent_responses.agent_id": {"$in": hybro_legacy}},
            {"$set": {"turns.$[].agent_responses.$[response].agent_id": "system:hybro"}},
            array_filters=[{"response.agent_id": {"$in": hybro_legacy}}]
        )
        total_memories_migrated += result.modified_count
        logger.info(f"Updated {result.modified_count} hybro memories.")

        # 3. Update run_events
        logger.info("Migrating run_events...")

        # Clarifier
        result = await db.run_events.update_many(
            {"agent_id": {"$in": clarifier_legacy}},
            {"$set": {"agent_id": "system:clarifier"}},
        )
        total_run_events_migrated += result.modified_count
        logger.info(f"Updated {result.modified_count} clarifier run events.")

        # Hybro
        result = await db.run_events.update_many(
            {"agent_id": {"$in": hybro_legacy}},
            {"$set": {"agent_id": "system:hybro"}},
        )
        total_run_events_migrated += result.modified_count
        logger.info(f"Updated {result.modified_count} hybro run events.")

        logger.info(f"Migration complete. Total agent messages migrated: {total_agent_messages_migrated}, Total memories migrated: {total_memories_migrated}, Total run events migrated: {total_run_events_migrated}")
    finally:
        await mongodb.close_database_connection()

if __name__ == "__main__":
    try:
        asyncio.run(run_migration())
    except KeyboardInterrupt:
        logger.info("Migration interrupted.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)
