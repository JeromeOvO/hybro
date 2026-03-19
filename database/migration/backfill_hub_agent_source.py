"""
Migration: Set source="hub" on agents that have a hub_id but source!="hub".

Some hub-synced agents were originally registered via the web UI before
hub sync was implemented.  When sync_agents found them by normalised URL
it enriched them with hub_id / local_agent_id but left source="cloud".
This backfill corrects those records.

Run with:
    python -m database.migration.backfill_hub_agent_source --dry-run
    python -m database.migration.backfill_hub_agent_source
"""

import asyncio
import sys

from common.utils.logger import get_logger
from database.mongodb import mongodb

logger = get_logger(__name__)


async def backfill_hub_agent_source(dry_run: bool = False):
    await mongodb.connect()
    if not mongodb.client:
        logger.error("MongoDB connection failed, aborting migration.")
        return

    try:
        agents_coll = mongodb.agents_collection

        query = {
            "hub_id": {"$exists": True, "$ne": None},
            "source": {"$ne": "hub"},
        }

        cursor = agents_coll.find(query, {"agent_id": 1, "source": 1, "hub_id": 1})
        affected: list[dict] = []
        async for doc in cursor:
            affected.append({
                "agent_id": doc["agent_id"],
                "current_source": doc.get("source"),
                "hub_id": doc.get("hub_id"),
            })

        logger.info("Found %d agents with hub_id but source != 'hub'.", len(affected))
        for a in affected[:20]:
            logger.info("  %-40s  source=%s  hub_id=%s", a["agent_id"], a["current_source"], a["hub_id"])
        if len(affected) > 20:
            logger.info("  ... and %d more agents", len(affected) - 20)

        if dry_run:
            logger.info("[DRY RUN] Would update %d agents. Run without --dry-run to apply.", len(affected))
            return

        if not affected:
            logger.info("Nothing to update.")
            return

        result = await agents_coll.update_many(query, {"$set": {"source": "hub"}})
        logger.info("Updated %d agents.", result.modified_count)

        remaining = await agents_coll.count_documents(query)
        if remaining == 0:
            logger.info("Verification passed: all hub agents now have source='hub'.")
        else:
            logger.warning("Verification: %d agents still have mismatched source.", remaining)

    finally:
        await mongodb.close_database_connection()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info("Running in DRY RUN mode — no changes will be made.")
    asyncio.run(backfill_hub_agent_source(dry_run=dry_run))
