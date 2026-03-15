"""
Migration: Create indexes for agent_capability_issues collection.

Usage:
    python -m database.migration.add_capability_issue_indexes
"""

import asyncio

from database.mongodb import mongodb


async def migrate():
    await mongodb.connect()
    await mongodb.create_capability_issue_indexes()
    print("Migration complete: capability issue indexes created.")
    await mongodb.close_database_connection()


if __name__ == "__main__":
    asyncio.run(migrate())
