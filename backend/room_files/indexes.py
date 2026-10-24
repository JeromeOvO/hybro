"""Indexes for the ``room_files`` metadata collection."""

from __future__ import annotations

from common.protocols import MongoDAL
from common.utils.mongo_indexes import create_index


async def ensure_room_file_indexes(mongo: MongoDAL) -> None:
    await create_index(
        mongo.collection("room_files"),
        "room_files",
        [("file_id", 1)],
        name="room_file_id_unique",
        unique=True,
        critical=True,
    )
    await create_index(
        mongo.collection("room_files"),
        "room_files",
        [("room_id", 1), ("created_at", -1)],
        name="room_file_room_created",
    )
    await create_index(
        mongo.collection("room_files"),
        "room_files",
        [("source_message_id", 1)],
        name="room_file_source_message",
        sparse=True,
    )
    await create_index(
        mongo.collection("room_files"),
        "room_files",
        [("origin_key", 1)],
        name="room_file_origin_unique",
        unique=True,
        partialFilterExpression={"origin_key": {"$type": "string"}},
    )
    await create_index(
        mongo.collection("room_files"),
        "room_files",
        [("status", 1), ("updated_at", 1)],
        name="room_file_status_updated",
    )
    await create_index(
        mongo.collection("room_files"),
        "room_files",
        [
            ("source", 1),
            ("status", 1),
            ("last_referenced_at", 1),
            ("created_at", 1),
        ],
        name="room_file_retention",
    )
    await create_index(
        mongo.collection("room_files"),
        "room_files",
        [("reference_claims.message_id", 1)],
        name="room_file_reference_message",
    )


__all__ = ["ensure_room_file_indexes"]
