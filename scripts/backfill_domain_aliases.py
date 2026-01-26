"""
Backfill Public URLs Migration Script

This script creates public_url values for existing agents that were
registered before the URL masking feature was implemented.

Usage:
    python -m scripts.backfill_domain_aliases

Options:
    --dry-run: Preview changes without making modifications
    --force: Skip confirmation prompt
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.utils.logger import get_logger
from database.mongodb import get_db, mongodb
from services.domain_alias_service import domain_alias_service

logger = get_logger(__name__)


async def backfill_public_urls(dry_run: bool = False) -> dict:
    """
    Generate public_url for existing agents that don't have one.
    
    Args:
        dry_run: If True, only preview changes without making modifications
        
    Returns:
        Dictionary with migration statistics
    """
    db = await get_db()

    stats = {
        "total_agents": 0,
        "created": 0,
        "failed": 0,
        "errors": []
    }

    # Get all agents that don't have a public_url
    all_agents = await db.agents.find({
        "$or": [
            {"public_url": {"$exists": False}},
            {"public_url": None}
        ]
    }).to_list(length=None)

    stats["total_agents"] = len(all_agents)

    logger.info(f"Found {len(all_agents)} agents without public_url")

    if dry_run:
        logger.info("=== DRY RUN MODE - No changes will be made ===")

    for agent_doc in all_agents:
        agent_id = agent_doc.get("agent_id")
        agent_card = agent_doc.get("agent_card", {})
        agent_name = agent_card.get("name")

        # Generate public URL
        if dry_run:
            # In dry run, just show what would be generated
            public_url = await domain_alias_service.generate_public_url(
                    agent_name=agent_name,
                    agent_id=agent_id
                )
            logger.info(f"[DRY RUN] Would create: {agent_name} -> {public_url}")
            stats["created"] += 1
        else:
            try:
                public_url = await domain_alias_service.generate_public_url(
                    agent_name=agent_name,
                    agent_id=agent_id
                )

                # Update agent with public_url
                await db.agents.update_one(
                    {"agent_id": agent_id},
                    {"$set": {"public_url": public_url}}
                )

                logger.info(f"Created public_url for '{agent_name}': {public_url}")
                stats["created"] += 1

            except Exception as e:
                error_msg = f"Failed to create public_url for agent {agent_id}: {str(e)}"
                logger.error(error_msg)
                stats["failed"] += 1
                stats["errors"].append(error_msg)

    return stats


async def main():
    parser = argparse.ArgumentParser(
        description="Backfill public URLs for existing agents"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without making modifications"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt"
    )

    args = parser.parse_args()

    # Connect to MongoDB first
    print("Connecting to MongoDB...")
    await mongodb.connect()
    
    if not mongodb.client:
        print("Failed to connect to MongoDB. Please check your MONGODB_URL environment variable.")
        return

    try:
        print("\n" + "=" * 60)
        print("  Public URL Backfill Migration")
        print("=" * 60)
        print(f"  Base Domain: {domain_alias_service.BASE_DOMAIN}")
        print(f"  Protocol: {domain_alias_service.PROTOCOL}")
        print(f"  Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
        print("=" * 60 + "\n")

        # Confirmation prompt
        if not args.force and not args.dry_run:
            confirm = input("This will generate public URLs for all existing agents. Continue? [y/N]: ")
            if confirm.lower() != 'y':
                print("Aborted.")
                return

        # Run migration
        stats = await backfill_public_urls(dry_run=args.dry_run)

        # Print summary
        print("\n" + "=" * 60)
        print("  Migration Complete")
        print("=" * 60)
        print(f"  Agents Processed:  {stats['total_agents']}")
        print(f"  URLs Created:      {stats['created']}")
        print(f"  Failed:            {stats['failed']}")
        print("=" * 60 + "\n")

        if stats["errors"]:
            print("Errors:")
            for error in stats["errors"]:
                print(f"  - {error}")
            print()
    finally:
        # Close MongoDB connection
        await mongodb.close_database_connection()
        print("MongoDB connection closed.")


if __name__ == "__main__":
    asyncio.run(main())
