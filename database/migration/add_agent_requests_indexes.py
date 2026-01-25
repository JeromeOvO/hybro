"""
Migration: Add indexes for agent_requests collection (rate limiting)

This migration creates the agent_requests collection with appropriate indexes:
- TTL index on timestamp (auto-cleanup after 2 hours)
- Compound index for per-user rate limit queries
- Index for system-wide rate limit queries

Run this script once to set up the collection:
    python -m database.migration.add_agent_requests_indexes
"""

import asyncio
import os
import sys

# Add parent directory to path so we can import from database
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from database.mongodb import mongodb


async def create_agent_requests_indexes():
    """Create indexes for agent_requests collection"""
    try:
        await mongodb.connect()
        collection = mongodb.agent_requests_collection

        print("Creating indexes for agent_requests collection...")

        # 1. TTL index: automatically delete records older than 2 hours
        # (sliding window only needs 1 hour, but 2 hours gives buffer)
        await collection.create_index(
            "timestamp",
            expireAfterSeconds=7200  # 2 hours
        )
        print("Created TTL index on timestamp (2 hours expiration)")

        # 2. Compound index for per-user rate limit queries
        # Optimizes: find requests by agent_id + user_id within time window
        await collection.create_index([
            ("agent_id", 1),
            ("user_id", 1),
            ("timestamp", -1)
        ])
        print("Created compound index on (agent_id, user_id, timestamp)")

        # 3. Index for system-wide rate limit queries
        # Optimizes: count all requests to an agent within time window
        await collection.create_index([
            ("agent_id", 1),
            ("timestamp", -1)
        ])
        print("Created index on (agent_id, timestamp)")

        print("Migration completed successfully!")

    except Exception as e:
        print(f"Migration failed: {e}")
        raise
    finally:
        await mongodb.close_database_connection()


if __name__ == "__main__":
    asyncio.run(create_agent_requests_indexes())
