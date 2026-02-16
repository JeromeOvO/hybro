"""
Migration: Add indexes for cancelled_messages collection

This migration creates the cancelled_messages collection with appropriate indexes:
- Unique index on message_id (primary lookup key)
- TTL index on cancelled_at (auto-cleanup after 3 days)

Run this script once to set up the collection:
    python -m database.migration.add_cancelled_messages_indexes
"""

import asyncio
import os
import sys

# Add parent directory to path so we can import from database
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from database.mongodb import mongodb


async def create_cancelled_messages_indexes():
    """Create indexes for cancelled_messages collection"""
    try:
        await mongodb.connect()
        collection = mongodb.cancelled_messages_collection

        print("Creating indexes for cancelled_messages collection...")

        # 1. Unique index on message_id (fast lookups)
        await collection.create_index("message_id", unique=True)
        print("Created unique index on message_id")

        # 2. TTL index on cancelled_at (auto-delete after 3 days)
        # MongoDB will automatically delete documents 3 days after cancelled_at timestamp
        await collection.create_index(
            "cancelled_at",
            expireAfterSeconds=3600 * 24 * 3,  # 3 days in seconds
        )
        print("Created TTL index on cancelled_at (3 days expiration)")

        # 3. Optional: Index on user_id for audit queries
        await collection.create_index("user_id")
        print("Created index on user_id")
    except Exception as e:
        print(f"Migration failed: {e}")
        raise
    finally:
        await mongodb.close_database_connection()


if __name__ == "__main__":
    asyncio.run(create_cancelled_messages_indexes())
