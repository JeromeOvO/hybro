"""
Rename legacy supervisor v2 field names in MongoDB documents.

Updates:
  - room_user_messages.extend_info.supervisor_v2 -> extend_info.supervisor
  - room_user_messages.extend_info.supervisor_v2_clarify_resume
      -> extend_info.supervisor_clarify_resume
  - room_user_messages.pending_continuation.supervisor_v2 -> .supervisor
  - room_agent_messages.pending_continuation.supervisor_v2 -> .supervisor

Run before or during deploy after the code rename (extend_info.supervisor).

Preview:
  python -m database.migration.rename_supervisor_v2_fields --dry-run

Apply:
  python -m database.migration.rename_supervisor_v2_fields
"""

from __future__ import annotations

import argparse
import asyncio

from database.mongodb import MongoDB


def _rename_supervisor_v2_in_doc(doc: dict) -> dict | None:
    """Return $set/$unset update ops, or None if no legacy fields present."""
    set_fields: dict = {}
    unset_fields: dict = {}

    extend_info = doc.get("extend_info")
    if isinstance(extend_info, dict):
        if "supervisor_v2" in extend_info and "supervisor" not in extend_info:
            set_fields["extend_info.supervisor"] = extend_info["supervisor_v2"]
        if "supervisor_v2" in extend_info:
            unset_fields["extend_info.supervisor_v2"] = ""

        if (
            "supervisor_v2_clarify_resume" in extend_info
            and "supervisor_clarify_resume" not in extend_info
        ):
            set_fields["extend_info.supervisor_clarify_resume"] = extend_info[
                "supervisor_v2_clarify_resume"
            ]
        if "supervisor_v2_clarify_resume" in extend_info:
            unset_fields["extend_info.supervisor_v2_clarify_resume"] = ""

    pending = doc.get("pending_continuation")
    if isinstance(pending, dict):
        if "supervisor_v2" in pending and "supervisor" not in pending:
            set_fields["pending_continuation.supervisor"] = pending["supervisor_v2"]
        if "supervisor_v2" in pending:
            unset_fields["pending_continuation.supervisor_v2"] = ""

    if not set_fields and not unset_fields:
        return None

    update: dict = {}
    if set_fields:
        update["$set"] = set_fields
    if unset_fields:
        update["$unset"] = unset_fields
    return update


async def _count_legacy(collection, label: str) -> int:
    query = {
        "$or": [
            {"extend_info.supervisor_v2": {"$exists": True}},
            {"extend_info.supervisor_v2_clarify_resume": {"$exists": True}},
            {"pending_continuation.supervisor_v2": {"$exists": True}},
        ]
    }
    count = await collection.count_documents(query)
    print(f"  {label}: {count} document(s) with legacy supervisor_v2 fields")
    return count


async def run_migration(*, dry_run: bool) -> None:
    db = MongoDB()
    await db.connect()

    if not db.client:
        print("MongoDB connection failed, aborting migration.")
        return

    collections = [
        ("room_user_messages", db.room_user_messages_collection),
        ("room_agent_messages", db.room_agent_messages_collection),
    ]

    print("=== Supervisor v2 field rename ===")
    print(f"Mode: {'dry-run' if dry_run else 'apply'}\n")

    total_found = 0
    for label, collection in collections:
        total_found += await _count_legacy(collection, label)

    if total_found == 0:
        print("\nNo legacy supervisor_v2 fields found. Migration complete.")
        await db.close_database_connection()
        return

    migrated = 0
    skipped = 0
    failed = 0

    for label, collection in collections:
        query = {
            "$or": [
                {"extend_info.supervisor_v2": {"$exists": True}},
                {"extend_info.supervisor_v2_clarify_resume": {"$exists": True}},
                {"pending_continuation.supervisor_v2": {"$exists": True}},
            ]
        }
        cursor = collection.find(query)
        async for doc in cursor:
            doc_id = doc.get("_id")
            message_id = doc.get("message_id", "unknown")
            update = _rename_supervisor_v2_in_doc(doc)
            if update is None:
                skipped += 1
                continue

            if dry_run:
                print(f"  [dry-run] would update {label} message_id={message_id}: {update}")
                migrated += 1
                continue

            try:
                result = await collection.update_one({"_id": doc_id}, update)
                if result.modified_count > 0:
                    migrated += 1
                else:
                    skipped += 1
            except Exception as exc:
                failed += 1
                print(f"  Error updating {label} message_id={message_id}: {exc}")

    print("\n=== Migration Complete ===")
    print(f"{'Would migrate' if dry_run else 'Migrated'}: {migrated}")
    print(f"Skipped: {skipped}")
    print(f"Failed: {failed}")

    await db.close_database_connection()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rename legacy supervisor_v2 MongoDB field names."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database.",
    )
    args = parser.parse_args()
    asyncio.run(run_migration(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
