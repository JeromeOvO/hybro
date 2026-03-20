"""
Backfill agent_status Migration Script

Sets agent_status = "active" on all agent documents that are missing
the field. These documents pre-date the introduction of agent_status
and are implicitly active (matching the Pydantic model default).

Without this migration, count_hub_agents() will under-count agents
because it now filters explicitly on agent_status.

Usage:
    # Dry run (default) — shows what would change, no writes:
    python -m scripts.backfill_agent_status

    # Apply changes:
    python -m scripts.backfill_agent_status --execute
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from common.utils.logger import get_logger
from database.mongodb import mongodb

logger = get_logger(__name__)


async def backfill_agent_status(dry_run: bool = True) -> dict:
    """Set agent_status = 'active' on all agents missing the field."""
    collection = mongodb.agents_collection

    query = {"agent_status": {"$exists": False}}
    affected = await collection.count_documents(query)

    stats = {"affected": affected, "updated": 0, "failed": 0}

    if affected == 0:
        logger.info("No agents missing agent_status — nothing to do.")
        return stats

    logger.info(
        "Found %d agent(s) missing agent_status%s.",
        affected,
        " (dry run)" if dry_run else "",
    )

    if dry_run:
        sample_cursor = collection.find(query, {"agent_id": 1, "agent_card.name": 1}).limit(10)
        samples = await sample_cursor.to_list(length=10)
        for doc in samples:
            name = doc.get("agent_card", {}).get("name", "<no name>")
            logger.info("[DRY RUN] Would update agent_id=%s name=%s", doc.get("agent_id"), name)
        if affected > 10:
            logger.info("[DRY RUN] ... and %d more.", affected - 10)
        return stats

    try:
        result = await collection.update_many(
            query,
            {"$set": {"agent_status": "active"}},
        )
        stats["updated"] = result.modified_count
        logger.info("Updated %d agent(s).", result.modified_count)
    except Exception as exc:
        logger.error("Bulk update failed: %s", exc)
        stats["failed"] = affected

    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill agent_status = 'active' for agents missing the field.\n"
            "Runs in dry-run mode by default; pass --execute to apply changes."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply changes. Omit to run in dry-run mode.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt (only relevant with --execute).",
    )
    args = parser.parse_args()
    dry_run = not args.execute

    print("Connecting to MongoDB...")
    await mongodb.connect()

    if not mongodb.client:
        print("Failed to connect to MongoDB. Check your MONGODB_URL environment variable.")
        sys.exit(1)

    try:
        print()
        print("=" * 60)
        print("  Backfill agent_status Migration")
        print("=" * 60)
        print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
        print("=" * 60)
        print()

        if not dry_run and not args.force:
            confirm = input(
                "This will set agent_status='active' on all agents missing the field. Continue? [y/N]: "
            )
            if confirm.lower() != "y":
                print("Aborted.")
                return

        stats = await backfill_agent_status(dry_run=dry_run)

        print()
        print("=" * 60)
        print("  Summary")
        print("=" * 60)
        print(f"  Agents missing agent_status:  {stats['affected']}")
        if not dry_run:
            print(f"  Updated:                      {stats['updated']}")
            print(f"  Failed:                       {stats['failed']}")
        print("=" * 60)
        print()

        if dry_run and stats["affected"] > 0:
            print("Run with --execute to apply the changes.")
    finally:
        await mongodb.close_database_connection()
        print("MongoDB connection closed.")


if __name__ == "__main__":
    asyncio.run(main())
