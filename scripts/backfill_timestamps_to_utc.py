import argparse
import asyncio
from datetime import UTC, datetime
from typing import Any

from database.mongodb import mongodb


def _normalize_timestamp(value: Any) -> str | None:
    """
    Convert a stored timestamp (str or datetime) into an ISO 8601 string with UTC offset.
    Falls back to None if parsing fails.
    """
    if value is None:
        return None

    try:
        if isinstance(value, str):
            # Accept both Z and +00:00
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif isinstance(value, datetime):
            parsed = value
        else:
            return None
    except Exception:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)

    return parsed.isoformat().replace("+00:00", "Z")


def _apply_normalization(
    doc: dict[str, Any], path: list[str], updates: dict[str, Any]
) -> None:
    """
    Normalize a timestamp at the specified path within the document and record it in updates.
    """
    cursor = doc
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            return
        cursor = cursor[key]

    normalized = _normalize_timestamp(cursor)
    if normalized and normalized != cursor:
        updates[".".join(path)] = normalized


async def normalize_timestamps(room_id: str | None, dry_run: bool) -> None:
    await mongodb.connect()

    rooms = mongodb.rooms_collection
    user_msgs = mongodb.room_user_messages_collection
    agent_msgs = mongodb.room_agent_messages_collection

    query = {"room_id": room_id} if room_id else {}

    updated_rooms = 0
    updated_user_msgs = 0
    updated_agent_msgs = 0

    # Rooms
    for room in await rooms.find(query if room_id else {}).to_list(length=None):
        updates: dict[str, Any] = {}
        _apply_normalization(room, ["room_created_at"], updates)

        if updates:
            if dry_run:
                print(
                    f"[DRY-RUN] room_id={room.get('room_id')} would update: {updates}"
                )
            else:
                await rooms.update_one({"_id": room["_id"]}, {"$set": updates})
            updated_rooms += 1

    # User messages
    for msg in await user_msgs.find(query).to_list(length=None):
        updates: dict[str, Any] = {}
        _apply_normalization(msg, ["message_created_at"], updates)
        _apply_normalization(
            msg, ["message_content", "message_task", "status", "timestamp"], updates
        )

        if updates:
            if dry_run:
                print(
                    f"[DRY-RUN] user_message_id={msg.get('message_id')} would update: {updates}"
                )
            else:
                await user_msgs.update_one({"_id": msg["_id"]}, {"$set": updates})
            updated_user_msgs += 1

    # Agent messages
    for msg in await agent_msgs.find(query).to_list(length=None):
        updates: dict[str, Any] = {}
        _apply_normalization(msg, ["message_created_at"], updates)
        _apply_normalization(
            msg, ["message_content", "message_task", "status", "timestamp"], updates
        )

        if updates:
            if dry_run:
                print(
                    f"[DRY-RUN] agent_message_id={msg.get('message_id')} would update: {updates}"
                )
            else:
                await agent_msgs.update_one({"_id": msg["_id"]}, {"$set": updates})
            updated_agent_msgs += 1

    print(
        f"{'DRY-RUN' if dry_run else 'COMPLETED'} | "
        f"rooms updated: {updated_rooms}, "
        f"user messages updated: {updated_user_msgs}, "
        f"agent messages updated: {updated_agent_msgs}"
    )

    await mongodb.close_database_connection()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Backfill room/message timestamps to UTC ISO format.\n"
            "Usage:\n"
            "  python scripts/backfill_timestamps_to_utc.py               (dry run)\n"
            "  python scripts/backfill_timestamps_to_utc.py --execute     (apply all)\n"
            "  python scripts/backfill_timestamps_to_utc.py --room-id <ROOM_ID>            (dry run room)\n"
            "  python scripts/backfill_timestamps_to_utc.py --room-id <ROOM_ID> --execute  (apply room)\n"
            "Notes: dry-run by default; timestamps normalized to ISO 8601 UTC (Z)."
        )
    )
    parser.add_argument(
        "--room-id",
        type=str,
        default=None,
        help="Optional room_id to limit the backfill to a single room.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply changes. If omitted, runs in dry-run mode.",
    )
    args = parser.parse_args()

    asyncio.run(normalize_timestamps(room_id=args.room_id, dry_run=not args.execute))
