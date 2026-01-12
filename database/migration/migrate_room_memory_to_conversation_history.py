"""
Migration script to convert legacy room memory format to new conversation history format.

Old format:
    memory_content: {
        memory_text: "some text..."
    }

New format:
    memory_content: {
        summary: "some text...",  # Moved from memory_text
        conversation_history: [],  # New field
        memory_text: null          # Cleared
    }

Run: python -m database.migration.migrate_room_memory_to_conversation_history
Preview: python -m database.migration.migrate_room_memory_to_conversation_history --dry-run
"""

import asyncio

from database.mongodb import MongoDB


async def run_migration():
    """Migrate legacy room memories to new conversation history format."""
    db = MongoDB()
    await db.connect()

    if not db.client:
        print("MongoDB connection failed, aborting migration.")
        return

    collection = db.room_memories_collection

    # Find all documents with legacy memory_text format
    # (has memory_text but no conversation_history)
    legacy_query = {
        "$and": [
            {"memory_content.memory_text": {"$exists": True, "$ne": None, "$ne": ""}},
            {
                "$or": [
                    {"memory_content.conversation_history": {"$exists": False}},
                    {"memory_content.conversation_history": {"$size": 0}},
                    {"memory_content.conversation_history": None},
                ]
            },
        ]
    }

    # Count documents to migrate
    total_count = await collection.count_documents(legacy_query)
    print(f"Found {total_count} room memories to migrate")

    if total_count == 0:
        print("No legacy room memories found. Migration complete.")
        await db.close_database_connection()
        return

    # Process documents
    migrated_count = 0
    failed_count = 0

    cursor = collection.find(legacy_query)

    async for doc in cursor:
        try:
            room_id = doc.get("room_id", "unknown")
            memory_id = doc.get("memory_id", "unknown")
            old_text = doc.get("memory_content", {}).get("memory_text", "")

            # Update document: move memory_text to summary, initialize conversation_history
            result = await collection.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "memory_content.summary": old_text,
                        "memory_content.conversation_history": [],
                        "memory_content.memory_text": None,
                    }
                },
            )

            if result.modified_count > 0:
                migrated_count += 1
                if migrated_count % 50 == 0:
                    print(f"Progress: {migrated_count}/{total_count} migrated")
            else:
                print(f"Warning: Document {memory_id} not modified")

        except Exception as e:
            failed_count += 1
            print(f"Error migrating room {room_id}: {e}")

    print(f"\n=== Migration Complete ===")
    print(f"Total found: {total_count}")
    print(f"Successfully migrated: {migrated_count}")
    print(f"Failed: {failed_count}")

    await db.close_database_connection()


async def dry_run():
    """Preview what would be migrated without making changes."""
    db = MongoDB()
    await db.connect()

    if not db.client:
        print("MongoDB connection failed.")
        return

    collection = db.room_memories_collection

    legacy_query = {
        "$and": [
            {"memory_content.memory_text": {"$exists": True, "$ne": None, "$ne": ""}},
            {
                "$or": [
                    {"memory_content.conversation_history": {"$exists": False}},
                    {"memory_content.conversation_history": {"$size": 0}},
                    {"memory_content.conversation_history": None},
                ]
            },
        ]
    }

    total_count = await collection.count_documents(legacy_query)
    print(f"\n=== Dry Run ===")
    print(f"Found {total_count} room memories that would be migrated")

    # Show sample
    if total_count > 0:
        print("\nSample documents to migrate:")
        cursor = collection.find(legacy_query).limit(3)
        async for doc in cursor:
            room_id = doc.get("room_id", "unknown")
            memory_text = doc.get("memory_content", {}).get("memory_text", "")
            preview = (
                memory_text[:100] + "..." if len(memory_text) > 100 else memory_text
            )
            print(f"  - Room {room_id}: {preview}")

    await db.close_database_connection()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--dry-run":
        print("Running in dry-run mode (no changes will be made)")
        asyncio.run(dry_run())
    else:
        print("Running migration...")
        asyncio.run(run_migration())
