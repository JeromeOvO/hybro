"""
Migration to:
1. Remove duplicate agents (keep oldest by _id)
2. Backfill normalized_url for remaining agents
3. Create unique index on normalized_url

Run with:
    python -m database.migration.deduplicate_agents --dry-run  # Preview changes
    python -m database.migration.deduplicate_agents            # Apply changes
"""

import asyncio
from collections import defaultdict
from urllib.parse import urlparse, urlunparse

from common.utils.logger import get_logger
from database.mongodb import get_db, mongodb

logger = get_logger(__name__)

# Local host aliases that should be normalized to "localhost"
LOCAL_HOST_ALIASES = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def normalize_agent_url(url: str) -> str:
    """Normalize an agent URL for consistent comparison."""
    if not url:
        return url

    for well_known_path in ["/.well-known/agent-card.json", "/.well-known/agent.json"]:
        if well_known_path in url:
            url = url.split(well_known_path)[0]

    try:
        parsed = urlparse(url)
    except Exception:
        return url

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return url

    # Normalize localhost aliases to canonical "localhost"
    if hostname in LOCAL_HOST_ALIASES:
        hostname = "localhost"

    port = parsed.port
    if (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    ):
        port = None

    netloc = hostname
    if port:
        netloc = f"{hostname}:{port}"

    path = parsed.path.rstrip("/")

    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


async def deduplicate_agents(dry_run: bool = False):
    """
    Remove duplicate agents and backfill normalized_url.
    """
    mongo_db = await get_db()

    # Step 1: Collect all agents and group by normalized URL
    logger.info("Step 1: Collecting all agents...")
    url_to_agents = defaultdict(list)
    agents_without_url = []

    cursor = mongo_db.agents.find({})
    total_count = 0

    async for doc in cursor:
        total_count += 1
        agent_id = doc.get("agent_id")
        agent_card_url = doc.get("agent_card", {}).get("url")

        if not agent_card_url:
            agents_without_url.append(agent_id)
            continue

        normalized = normalize_agent_url(agent_card_url)
        url_to_agents[normalized].append(
            {
                "agent_id": agent_id,
                "original_url": agent_card_url,
                "provider_id": doc.get("provider_id"),
                "agent_status": doc.get("agent_status", "active"),
                "_id": doc.get("_id"),
            }
        )

    logger.info(f"Found {total_count} total agents")
    logger.info(f"Found {len(agents_without_url)} agents without agent_card.url")

    # Step 2: Identify duplicates
    duplicates = {
        url: agents for url, agents in url_to_agents.items() if len(agents) > 1
    }
    unique_urls = {
        url: agents[0] for url, agents in url_to_agents.items() if len(agents) == 1
    }

    logger.info(f"Found {len(unique_urls)} unique URLs")
    logger.info(f"Found {len(duplicates)} URLs with duplicates")

    # Step 3: Report duplicates
    agents_to_delete = []
    agents_to_keep = []

    if duplicates:
        logger.warning("=" * 60)
        logger.warning("DUPLICATE AGENTS FOUND")
        logger.warning("=" * 60)

        for normalized_url, agents in duplicates.items():
            # Sort by _id (oldest first based on MongoDB ObjectId)
            sorted_agents = sorted(agents, key=lambda x: x["_id"])

            # Keep the oldest one
            primary = sorted_agents[0]
            agents_to_keep.append((normalized_url, primary))

            logger.info(f"\nURL: {normalized_url}")
            logger.info(
                f"  KEEP: agent_id={primary['agent_id']}, provider={primary['provider_id']}"
            )

            # Mark others for deletion
            for dup in sorted_agents[1:]:
                agents_to_delete.append(dup)
                logger.warning(
                    f"  DELETE: agent_id={dup['agent_id']}, provider={dup['provider_id']}"
                )

    logger.info(f"\nTotal agents to delete: {len(agents_to_delete)}")
    logger.info(
        f"Total agents to keep and update: {len(unique_urls) + len(agents_to_keep)}"
    )

    if dry_run:
        logger.info(
            "\n[DRY RUN] No changes made. Run without --dry-run to apply changes."
        )
        return

    # Step 4: Delete duplicate agents
    logger.info("\nStep 4: Deleting duplicate agents...")
    deleted_count = 0

    for agent in agents_to_delete:
        result = await mongo_db.agents.delete_one({"_id": agent["_id"]})
        if result.deleted_count > 0:
            deleted_count += 1
            logger.info(f"Deleted agent {agent['agent_id']}")
        else:
            logger.warning(f"Failed to delete agent {agent['agent_id']}")

    logger.info(f"Deleted {deleted_count} duplicate agents")

    # Step 5: Update unique agents with normalized_url
    logger.info("\nStep 5: Updating agents with normalized_url...")
    updated_count = 0

    # Update unique agents
    for normalized_url, agent in unique_urls.items():
        await mongo_db.agents.update_one(
            {"_id": agent["_id"]}, {"$set": {"normalized_url": normalized_url}}
        )
        updated_count += 1

    # Update kept agents from duplicates
    for normalized_url, agent in agents_to_keep:
        await mongo_db.agents.update_one(
            {"_id": agent["_id"]}, {"$set": {"normalized_url": normalized_url}}
        )
        updated_count += 1

    logger.info(f"Updated {updated_count} agents with normalized_url")

    # Step 6: Create unique index
    logger.info("\nStep 6: Creating unique index on normalized_url...")
    try:
        await mongo_db.agents.create_index(
            "normalized_url",
            unique=True,
            sparse=True,  # Allow null for agents without URL
            name="unique_normalized_url",
        )
        logger.info("Successfully created unique index")
    except Exception as e:
        logger.error(f"Failed to create index: {e}")
        raise

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("MIGRATION COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Agents deleted: {deleted_count}")
    logger.info(f"Agents updated: {updated_count}")
    logger.info(f"Agents without URL (skipped): {len(agents_without_url)}")


async def main():
    import sys

    dry_run = "--dry-run" in sys.argv

    # Connect to MongoDB first
    await mongodb.connect()

    try:
        await deduplicate_agents(dry_run=dry_run)
    finally:
        await mongodb.close_database_connection()


if __name__ == "__main__":
    asyncio.run(main())
