import asyncio
from database.mongodb import MongoDB

# run python -m add_provider_legacy_flag for database migration

async def run_migration():
    db = MongoDB()
    await db.connect()

    if not db.client:
        print("MongoDB connection failed, aborting migration.")
        return

    agents_collection = db.agents_collection

    #
    result = await agents_collection.update_many(
        {"provider_id": {"$exists": False}},
        {"$set": {"provider_id": None, "is_legacy": True}},
    )

    print(f"Migrated {result.modified_count} legacy agents.")
    await db.close_database_connection()

if __name__ == "__main__":
    asyncio.run(run_migration())
