"""
Migration: Add unique index on message_id for room_user_messages collection

This migration is a prerequisite for the idempotency guard (SDR 2.5).
It ensures no duplicate message_id values exist before creating the unique index.

Run this script once before deploying the claim logic:
    python -m database.migration.add_user_message_id_unique_index
"""

import asyncio
import os
import sys

# Add parent directory to path so we can import from database
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from database.mongodb import mongodb


async def create_unique_message_id_index():
    """Create unique index on message_id after verifying no duplicates exist."""
    try:
        await mongodb.connect()
        collection = mongodb.room_user_messages_collection

        # Step 1: Check for duplicate message_id values
        print("Checking for duplicate message_id values...")
        pipeline = [
            {"$group": {"_id": "$message_id", "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
        ]
        duplicates = await collection.aggregate(pipeline).to_list(length=None)

        if duplicates:
            print("ERROR: Found duplicate message_id values. Manual dedup required:")
            for dup in duplicates:
                print(f"  message_id={dup['_id']} count={dup['count']}")
            sys.exit(1)

        print("No duplicates found.")

        # Step 2: Create the unique index
        print("Creating unique index on message_id...")
        await collection.create_index(
            "message_id", unique=True, name="idx_message_id_unique"
        )
        print("Created unique index idx_message_id_unique on message_id")

    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)
    finally:
        await mongodb.close_database_connection()


if __name__ == "__main__":
    asyncio.run(create_unique_message_id_index())
