from __future__ import annotations

from datetime import datetime
from typing import Any

from common.protocols import MongoDAL
from room.message_graph import normalize_history_rows, status_update_payload


class RoomMongoRepository:
    def __init__(self, mongo: MongoDAL, collection_name: str = "rooms") -> None:
        self._rooms = mongo.collection(collection_name)

    async def get_by_id(self, room_id: str) -> dict | None:
        return await self._rooms.find_one({"room_id": room_id})

    async def get_by_owner(self, owner_id: str) -> list[dict]:
        return await self._rooms.find({"room_owner_id": owner_id})

    async def create(self, room: dict) -> str:
        inserted_id = await self._rooms.insert_one(dict(room))
        return str(room.get("room_id") or inserted_id)

    async def update(self, room_id: str, updates: dict) -> bool:
        return await self._rooms.update_one({"room_id": room_id}, {"$set": dict(updates)})

    async def update_fields(self, room_id: str, updates: dict) -> dict | None:
        return await self._rooms.find_one_and_update(
            {"room_id": room_id},
            {"$set": dict(updates)},
            return_document=True,
        )

    async def set_membership(
        self,
        room_id: str,
        *,
        agent_set: dict[str, str],
        membership_origin: str,
        membership_origin_status: str,
        source_group_id: str | None = None,
        source_group_name: str | None = None,
    ) -> dict | None:
        return await self.update_fields(
            room_id,
            {
                "room_agent_set": dict(agent_set),
                "membership_origin": membership_origin,
                "membership_origin_status": membership_origin_status,
                "source_group_id": source_group_id,
                "source_group_name": source_group_name,
            },
        )

    async def delete(self, room_id: str) -> bool:
        return await self._rooms.delete_one({"room_id": room_id})


class MessageMongoRepository:
    def __init__(
        self,
        mongo: MongoDAL,
        user_collection_name: str = "room_user_messages",
        agent_collection_name: str = "room_agent_messages",
    ) -> None:
        self._mongo = mongo
        self._user_messages = mongo.collection(user_collection_name)
        self._agent_messages = mongo.collection(agent_collection_name)
        self._cancelled_messages = None

    async def save_user_message(self, message: dict) -> str:
        inserted_id = await self._user_messages.insert_one(dict(message))
        return str(message.get("message_id") or inserted_id)

    async def save_agent_message(self, message: dict) -> str:
        inserted_id = await self._agent_messages.insert_one(dict(message))
        return str(message.get("message_id") or inserted_id)

    async def get_by_id(self, message_id: str) -> dict | None:
        user_message = await self._user_messages.find_one({"message_id": message_id})
        if user_message is not None:
            return user_message
        return await self._agent_messages.find_one({"message_id": message_id})

    async def is_message_cancelled(self, message_id: str) -> bool:
        if self._cancelled_messages is None:
            self._cancelled_messages = self._mongo.collection("cancelled_messages")
        return (
            await self._cancelled_messages.find_one({"message_id": message_id})
        ) is not None

    async def get_by_ids(self, message_ids: list[str]) -> list[dict]:
        if not message_ids:
            return []
        query = {"message_id": {"$in": list(message_ids)}}
        user_messages = await self._user_messages.find(query)
        agent_messages = await self._agent_messages.find(query)
        by_id = {
            str(row.get("message_id")): row
            for row in [*user_messages, *agent_messages]
            if row.get("message_id") is not None
        }
        return [by_id[message_id] for message_id in message_ids if message_id in by_id]

    async def get_for_room(
        self, room_id: str, limit: int, before: datetime | None = None
    ) -> list[dict]:
        user_messages = await self.get_user_messages_for_room(room_id, before=before)
        agent_messages = await self.get_agent_messages_for_room(room_id, before=before)
        return normalize_history_rows(user_messages, agent_messages)[:limit]

    async def get_user_message_by_id(self, message_id: str) -> dict | None:
        return await self._user_messages.find_one({"message_id": message_id})

    async def get_agent_message_by_id(self, message_id: str) -> dict | None:
        return await self._agent_messages.find_one({"message_id": message_id})

    async def get_user_messages_for_room(
        self, room_id: str, limit: int = 100, before: datetime | None = None
    ) -> list[dict]:
        query = _room_message_query(room_id, before)
        return await self._user_messages.find(query)

    async def get_agent_messages_for_room(
        self, room_id: str, limit: int = 100, before: datetime | None = None
    ) -> list[dict]:
        query = _room_message_query(room_id, before)
        return await self._agent_messages.find(query)

    async def get_thread(self, parent_message_id: str) -> list[dict]:
        thread: list[dict[str, Any]] = []
        frontier = [parent_message_id]
        seen = {parent_message_id}

        while frontier:
            query = {
                "$or": [
                    {"related_message_id": {"$in": frontier}},
                    {"parent_message_id": {"$in": frontier}},
                ]
            }
            rows = [
                *await self._user_messages.find(query),
                *await self._agent_messages.find(query),
            ]
            next_frontier: list[str] = []
            for row in normalize_history_rows([], rows):
                message_id = row.get("message_id")
                if not message_id or message_id in seen:
                    continue
                seen.add(message_id)
                thread.append(row)
                next_frontier.append(str(message_id))
            frontier = next_frontier

        return thread

    async def update_status(self, message_id: str, status: str, **fields) -> bool:
        payload = status_update_payload(status, fields)
        return await self._agent_messages.update_one(
            {"message_id": message_id},
            {"$set": payload},
        )

    async def update_user_message(self, message_id: str, updates: dict) -> bool:
        return await self._user_messages.update_one(
            {"message_id": message_id},
            {"$set": dict(updates)},
        )

    async def update_agent_message(self, message_id: str, updates: dict) -> bool:
        return await self._agent_messages.update_one(
            {"message_id": message_id},
            {"$set": dict(updates)},
        )

    async def delete_for_room(self, room_id: str) -> dict[str, int]:
        user_count = await self._user_messages.delete_many({"room_id": room_id})
        agent_count = await self._agent_messages.delete_many({"room_id": room_id})
        return {"user_messages": user_count, "agent_messages": agent_count}


def _room_message_query(room_id: str, before: datetime | None) -> dict[str, Any]:
    query: dict[str, Any] = {"room_id": room_id}
    if before is not None:
        query["message_created_at"] = {"$lt": before}
    return query
