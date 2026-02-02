"""
Migration: Add indexes for discovery_api_requests collection (rate limiting)

This migration creates the discovery_api_requests collection with appropriate indexes:
- TTL index on timestamp (auto-cleanup after 2 hours)
- Index for per-key rate limit queries
- Index for global rate limit queries

Run this script once to set up the collection:
    python -m database.migration.add_discovery_api_requests_indexes
"""

import asyncio
import os
import sys

# Add parent directory to path so we can import from database
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from database.mongodb import mongodb


async def create_discovery_api_requests_indexes():
    """Create indexes for discovery_api_requests collection"""
    try:
        await mongodb.connect()
        collection = mongodb.discovery_api_requests_collection

        print("Creating indexes for discovery_api_requests collection...")

        # 1. TTL index: automatically delete records older than 2 hours
        await collection.create_index(
            "timestamp",
            expireAfterSeconds=7200  # 2 hours
        )
        print("Created TTL index on timestamp (2 hours expiration)")

        # 2. Compound index for per-key rate limit queries
        # Optimizes: find requests by key_id within time window
        await collection.create_index([
            ("key_id", 1),
            ("timestamp", -1)
        ])
        print("Created compound index on (key_id, timestamp)")

        # 3. Index for global rate limit queries
        # Optimizes: count all requests within time window
        await collection.create_index([
            ("timestamp", -1)
        ])
        print("Created index on timestamp")

        print("Migration completed successfully!")

    except Exception as e:
        print(f"Migration failed: {e}")
        raise
    finally:
        await mongodb.close_database_connection()


if __name__ == "__main__":
    asyncio.run(create_discovery_api_requests_indexes())

