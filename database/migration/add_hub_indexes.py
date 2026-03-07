"""
Migration: Add indexes for hubs collection and hub-agent indexes on agents collection

Creates:
- hubs.hub_id (unique)
- hubs.user_id
- agents.(hub_id, local_agent_id) unique partial index (where both are non-null)
- agents.(hub_id, source) for fast hub agent lookups

Run this script once:
    python -m database.migration.add_hub_indexes
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from database.mongodb import mongodb


async def create_hub_indexes():
    """Create indexes for hubs collection and hub-related agent indexes."""
    try:
        await mongodb.connect()

        # -- hubs collection --
        hubs = mongodb.hubs_collection
        print("Creating indexes for hubs collection...")

        await hubs.create_index("hub_id", unique=True)
        print("  Created unique index on hub_id")

        await hubs.create_index("user_id")
        print("  Created index on user_id")

        # -- agents collection (hub fields) --
        agents = mongodb.agents_collection
        print("Creating hub-related indexes on agents collection...")

        await agents.create_index(
            [("hub_id", 1), ("local_agent_id", 1)],
            unique=True,
            partialFilterExpression={
                "hub_id": {"$type": "string"},
                "local_agent_id": {"$type": "string"},
            },
        )
        print("  Created unique partial index on (hub_id, local_agent_id)")

        await agents.create_index([("hub_id", 1), ("source", 1)])
        print("  Created compound index on (hub_id, source)")

        print("Migration completed successfully!")

    except Exception as e:
        print(f"Migration failed: {e}")
        raise
    finally:
        await mongodb.close_database_connection()


if __name__ == "__main__":
    asyncio.run(create_hub_indexes())
