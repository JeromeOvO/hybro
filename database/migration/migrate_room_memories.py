"""
Migration script for room_memories to new context memory system schema.

This script migrates existing room_memories documents to the new schema defined in
CONTEXT_MEMORY_SYSTEM_DESIGN.md §4.2. It handles:

1. Adding new fields to RoomMemory:
   - conversation_history (direct list, not nested in memory_content)
   - room_summary (RoomSummary)
   - room_facts (list[RoomFact])
   - agent_success_history (dict[str, AgentSuccessRecord])
   - last_activity_at, total_messages, total_compactions

2. Migrating existing ConversationTurn documents to new schema:
   - Adding turn_id (UUID)
   - Adding representation ("full")
   - Adding estimated_tokens_full (computed via estimate_tokens)
   - Adding turn_notes (computed via extract_turn_notes)
   - Adding content_type, turn_type defaults

3. Preserving backward compatibility:
   - memory_content is kept for backward compatibility
   - conversation_history is populated as the new canonical location

Usage:
  python -m database.migration.migrate_room_memories               (dry run)
  python -m database.migration.migrate_room_memories --execute     (apply all)
  python -m database.migration.migrate_room_memories --room-id <ROOM_ID>            (dry run room)
  python -m database.migration.migrate_room_memories --room-id <ROOM_ID> --execute  (apply room)

Notes:
  - Dry-run by default; use --execute to apply changes
  - Safe to run multiple times (idempotent)
  - Creates indexes on first run
  - Standalone: does NOT import the app's mongodb singleton (avoids a2a dependency)
"""

import argparse
import asyncio
import os
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()


def _get_mongo_client_and_db():
    """Create a standalone MongoDB connection from env vars."""
    url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME")
    if not url:
        raise ValueError("MONGODB_URL environment variable is not set")
    if not db_name:
        raise ValueError("MONGODB_DB_NAME environment variable is not set")
    client = AsyncIOMotorClient(url)
    return client, client[db_name]


def estimate_tokens_simple(text: str | None) -> int:
    """Simple token estimation (~4 chars per token for English)."""
    if not text:
        return 0
    return len(text) // 4


_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare",
    "ought", "used", "to", "of", "in", "for", "on", "with", "at", "by",
    "from", "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "under", "again", "further", "then", "once", "here",
    "there", "when", "where", "why", "how", "all", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own",
    "same", "so", "than", "too", "very", "just", "and", "but", "if", "or",
    "because", "until", "while", "this", "that", "these", "those", "i",
    "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "them", "my", "your", "his", "its", "our", "their", "what", "which",
    "who", "whom", "please", "thanks", "thank", "yes", "no", "okay", "ok",
}


def extract_turn_notes_simple(content: str | None) -> dict | None:
    """
    Simple heuristic extraction of turn notes.
    Matches the implementation in context_utils.py.
    """
    if not content or len(content.strip()) < 10:
        return None

    words = content.split()

    keywords: list[str] = []
    seen: set[str] = set()
    for word in words:
        clean_word = re.sub(r"[^\w]", "", word.lower())
        if (
            len(clean_word) > 4
            and clean_word not in _STOP_WORDS
            and clean_word not in seen
            and clean_word.isalpha()
        ):
            keywords.append(clean_word)
            seen.add(clean_word)
            if len(keywords) >= 10:
                break

    entities: list[str] = []
    entity_seen: set[str] = set()
    for i, word in enumerate(words):
        if i == 0:
            continue
        prev_word = words[i - 1] if i > 0 else ""
        if prev_word.endswith((".", "!", "?")):
            continue

        clean_word = re.sub(r"[^\w]", "", word)
        if (
            clean_word
            and clean_word[0].isupper()
            and clean_word.lower() not in _STOP_WORDS
            and clean_word not in entity_seen
        ):
            entities.append(clean_word)
            entity_seen.add(clean_word)
            if len(entities) >= 5:
                break

    one_liner = content.strip()
    for end_char in [".", "!", "?"]:
        idx = one_liner.find(end_char)
        if idx > 0 and idx < 150:
            one_liner = one_liner[: idx + 1]
            break
    else:
        if len(one_liner) > 100:
            one_liner = one_liner[:100] + "..."

    return {
        "keywords": keywords,
        "entities": entities,
        "tags": [],
        "one_liner": one_liner,
    }


def migrate_turn(turn: dict[str, Any]) -> dict[str, Any]:
    """
    Migrate a single ConversationTurn to the new schema.

    Adds new fields while preserving existing ones.
    """
    if turn.get("turn_id"):
        return turn

    content = turn.get("content", "")

    turn["turn_id"] = str(uuid4())
    turn["representation"] = "full"
    turn["content_type"] = "text"
    turn["turn_type"] = "message"
    turn["estimated_tokens_full"] = estimate_tokens_simple(content)
    turn["estimated_tokens_compact"] = 20
    turn["turn_notes"] = extract_turn_notes_simple(content)
    turn["content_ref"] = None
    turn["brief_summary"] = None
    turn["was_successful"] = None

    return turn


def migrate_room_memory(doc: dict[str, Any]) -> dict[str, Any]:
    """
    Migrate a RoomMemory document to the new schema.

    Returns the updates dict to apply via $set.
    """
    updates: dict[str, Any] = {}

    memory_content = doc.get("memory_content", {})
    existing_history = memory_content.get("conversation_history", [])

    migrated_history = [migrate_turn(turn) for turn in existing_history]

    if migrated_history != existing_history:
        updates["memory_content.conversation_history"] = migrated_history

    if not doc.get("conversation_history"):
        updates["conversation_history"] = migrated_history

    if not doc.get("room_summary"):
        updates["room_summary"] = {
            "current_goal": None,
            "key_decisions": [],
            "open_questions": [],
            "recent_agent_contributions": [],
            "important_constraints": [],
            "last_updated_at": None,
            "updated_after_turn_id": None,
        }

    if not doc.get("room_facts"):
        updates["room_facts"] = []

    if not doc.get("agent_success_history"):
        updates["agent_success_history"] = {}

    if not doc.get("last_activity_at"):
        updates["last_activity_at"] = doc.get(
            "memory_created_at", datetime.now(tz=None).isoformat()
        )

    if doc.get("total_messages") is None:
        updates["total_messages"] = len(existing_history)

    if doc.get("total_compactions") is None:
        updates["total_compactions"] = 0

    return updates


async def create_context_memory_indexes(db: Any) -> None:
    """Create indexes required by the context memory system (mirrors mongodb.py)."""
    try:
        content_coll = db["conversation_content"]

        await content_coll.create_index(
            [("room_id", 1), ("turn_id", 1)],
            unique=True,
            name="room_turn_unique",
        )
        await content_coll.create_index(
            [("room_id", 1), ("stored_at", -1)],
            name="room_stored_at",
        )
        await content_coll.create_index(
            [
                ("content", "text"),
                ("turn_notes.keywords", "text"),
                ("turn_notes.entities", "text"),
                ("turn_notes.one_liner", "text"),
            ],
            name="turn_notes_text",
        )
        await content_coll.create_index(
            "expires_at",
            expireAfterSeconds=0,
            sparse=True,
            name="content_ttl",
        )
        print("  indexes created on conversation_content")

        await db["user_memories"].create_index(
            "user_id", unique=True, name="user_id_unique"
        )
        print("  indexes created on user_memories")

        await db["agent_memories"].create_index(
            "agent_id", unique=True, name="agent_id_unique"
        )
        print("  indexes created on agent_memories")

    except Exception as e:
        print(f"  [WARN] Error creating indexes (may already exist): {e}")


async def migrate_room_memories(room_id: str | None, dry_run: bool) -> None:
    """Run the migration."""
    client, db = _get_mongo_client_and_db()

    try:
        if not dry_run:
            print("Creating context memory indexes...")
            await create_context_memory_indexes(db)

        room_memories = db["room_memories"]
        query = {"room_id": room_id} if room_id else {}

        updated_count = 0
        skipped_count = 0
        error_count = 0

        cursor = room_memories.find(query)
        docs = await cursor.to_list(length=None)

        print(f"Found {len(docs)} room_memories to process")

        for doc in docs:
            try:
                updates = migrate_room_memory(doc)

                if not updates:
                    skipped_count += 1
                    continue

                if dry_run:
                    print(
                        f"  [DRY-RUN] room_id={doc.get('room_id')} "
                        f"would update {len(updates)} fields: {list(updates.keys())}"
                    )
                else:
                    await room_memories.update_one(
                        {"_id": doc["_id"]}, {"$set": updates}
                    )

                updated_count += 1

            except Exception as e:
                error_count += 1
                print(f"  [ERROR] room_id={doc.get('room_id')}: {e}")

        print(
            f"\n{'DRY-RUN' if dry_run else 'COMPLETED'} | "
            f"updated: {updated_count}, "
            f"skipped: {skipped_count}, "
            f"errors: {error_count}"
        )
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate room_memories to new context memory system schema."
    )
    parser.add_argument(
        "--room-id",
        type=str,
        default=None,
        help="Optional room_id to limit the migration to a single room.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply changes. If omitted, runs in dry-run mode.",
    )
    args = parser.parse_args()

    asyncio.run(
        migrate_room_memories(room_id=args.room_id, dry_run=not args.execute)
    )
