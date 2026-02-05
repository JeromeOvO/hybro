"""
Migration: Add A2A Tasks Indexes

This migration creates the necessary indexes for the a2a_tasks collection
used for long-running task tracking.
"""

import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()


async def migrate():
    """Create indexes for a2a_tasks collection."""
    mongodb_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME")

    if not mongodb_url or not db_name:
        print("Error: MONGODB_URL and MONGODB_DB_NAME must be set")
        return

    client = AsyncIOMotorClient(mongodb_url)
    db = client[db_name]
    collection = db.a2a_tasks

    print("Creating indexes for a2a_tasks collection...")

    try:
        # Primary queries
        await collection.create_index("room_id")
        print("  Created index: room_id")

        await collection.create_index("user_id")
        print("  Created index: user_id")

        await collection.create_index("task.status.state")
        print("  Created index: task.status.state")

        # Prevent duplicate tasks from same agent
        await collection.create_index(
            [("agent_url", 1), ("task.id", 1)],
            unique=True,
            sparse=True,
        )
        print("  Created unique index: agent_url + task.id")

        # Stale task detection
        await collection.create_index(
            [("updated_at", 1), ("task.status.state", 1)],
        )
        print("  Created index: updated_at + task.status.state")

        # TTL index: Auto-delete completed tasks after 30 days
        await collection.create_index(
            "updated_at",
            expireAfterSeconds=2592000,  # 30 days
            partialFilterExpression={
                "task.status.state": {
                    "$in": ["completed", "failed", "canceled", "rejected"]
                }
            },
        )
        print("  Created TTL index: updated_at (30 days for terminal states)")

        print("\nAll indexes created successfully!")

    except Exception as e:
        print(f"Error creating indexes: {e}")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(migrate())
