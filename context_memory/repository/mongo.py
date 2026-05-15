from __future__ import annotations

from datetime import datetime
from typing import Any

from common.protocols import ContentStorageRepository, MemoryRepository, MongoCollection, MongoDAL


class MemoryMongoRepository:
    def __init__(
        self,
        *,
        mongo: MongoDAL,
        collection_name: str = "room_memories",
        user_collection_name: str = "user_memories",
    ) -> None:
        self._memories = mongo.collection(collection_name)
        self._user_memories = mongo.collection(user_collection_name)

    async def get_room_memory(self, room_id: str) -> dict | None:
        return await self._memories.find_one({"room_id": room_id})

    async def upsert_room_memory(self, room_id: str, memory: dict) -> None:
        await self._memories.update_one(
            {"room_id": room_id},
            {"$set": _sanitize_update({**memory, "room_id": room_id})},
            upsert=True,
        )

    async def get_user_memories(self, user_id: str) -> list[dict]:
        return await self._user_memories.find({"user_id": user_id})

    async def delete_room_memory(self, room_id: str) -> bool:
        return await self._memories.delete_one({"room_id": room_id})

    async def create_room_memory(self, memory: dict) -> str:
        memory_id = memory.get("memory_id")
        if not memory_id:
            raise ValueError("memory_id is required")
        await self._memories.insert_one(dict(memory))
        return str(memory_id)

    async def ensure_room_memory(self, room_id: str, defaults: dict) -> dict:
        defaults = dict(defaults)
        defaults["room_id"] = room_id
        doc = await self._memories.find_one_and_update(
            {"room_id": room_id},
            {"$setOnInsert": defaults},
            upsert=True,
            return_document=True,
        )
        if doc is None:
            doc = await self.get_room_memory(room_id)
        return doc or defaults

    async def get_room_memory_by_memory_id(self, memory_id: str) -> dict | None:
        return await self._memories.find_one({"memory_id": memory_id})

    async def update_room_memory_by_room_id(
        self, room_id: str, updates: dict
    ) -> bool:
        return await self._memories.update_one(
            {"room_id": room_id},
            {"$set": _sanitize_update(updates)},
        )

    async def update_room_memory_by_memory_id(
        self, memory_id: str, updates: dict
    ) -> bool:
        return await self._memories.update_one(
            {"memory_id": memory_id},
            {"$set": _sanitize_update(updates)},
        )

    async def delete_room_memory_by_memory_id(self, memory_id: str) -> bool:
        return await self._memories.delete_one({"memory_id": memory_id})

    async def push_and_trim_conversation_turn(
        self,
        room_id: str,
        turn: dict,
        *,
        max_turns: int,
        summary_stub: str,
        max_summary_chars: int,
    ) -> tuple[bool, bool]:
        doc = await self._push_turn(room_id, turn, max_turns, summary_stub, max_summary_chars)
        matched = doc is not None
        return matched, matched

    async def push_and_trim_conversation_turn_if_absent(
        self,
        room_id: str,
        turn: dict,
        *,
        turn_id: str,
        max_turns: int,
        summary_stub: str,
        max_summary_chars: int,
    ) -> tuple[bool, bool, bool]:
        query = {
            "room_id": room_id,
            "memory_content.conversation_history.turn_id": {"$ne": turn_id},
            "conversation_history.turn_id": {"$ne": turn_id},
        }
        doc = await self._push_turn(
            room_id, turn, max_turns, summary_stub, max_summary_chars, query=query
        )
        if doc is not None:
            return True, True, False
        existing = await self.get_room_memory(room_id)
        if not existing:
            return False, False, False
        if _history_contains_turn(existing, turn_id):
            return False, True, True
        retry = await self._push_turn(
            room_id, turn, max_turns, summary_stub, max_summary_chars, query=query
        )
        if retry is not None:
            return True, True, False
        raise RuntimeError(f"Could not determine projection state for turn {turn_id}")

    async def update_turn_notes(
        self, room_id: str, turn_id: str, turn_notes: dict
    ) -> bool:
        ok = await self._memories.update_one(
            {"room_id": room_id, "memory_content.conversation_history.turn_id": turn_id},
            {"$set": {"memory_content.conversation_history.$.turn_notes": turn_notes}},
        )
        if ok:
            return True
        return await self._memories.update_one(
            {"room_id": room_id, "conversation_history.turn_id": turn_id},
            {"$set": {"conversation_history.$.turn_notes": turn_notes}},
        )

    async def get_room_summary_projection(self, room_id: str) -> dict | None:
        return await self._memories.find_one(
            {"room_id": room_id},
            projection={"room_summary": 1, "room_facts": 1, "room_id": 1},
        )

    async def update_room_summary_atomic(
        self,
        room_id: str,
        room_summary: dict,
        *,
        new_facts: list[dict] | None = None,
        max_facts: int = 50,
    ) -> bool:
        update: dict[str, Any] = {"$set": {"room_summary": room_summary}}
        if new_facts:
            update["$push"] = {
                "room_facts": {"$each": new_facts, "$slice": -max_facts}
            }
        return await self._memories.update_one({"room_id": room_id}, update)

    async def compact_turns_bulk(
        self, room_id: str, compacted_turns: list[dict]
    ) -> bool:
        for entry in compacted_turns:
            for path in (
                "memory_content.conversation_history",
                "conversation_history",
            ):
                await self._memories.update_one(
                    {"room_id": room_id},
                    {
                        "$set": {
                            f"{path}.$[turn].representation": "compact",
                            f"{path}.$[turn].content": None,
                            f"{path}.$[turn].content_ref": entry["content_ref"],
                            f"{path}.$[turn].estimated_tokens_compact": entry.get(
                                "estimated_tokens_compact", 20
                            ),
                        }
                    },
                    array_filters=[{"turn.turn_id": entry["turn_id"]}],
                )
        return await self._memories.update_one(
            {"room_id": room_id},
            {"$inc": {"total_compactions": 1}, "$set": {"last_activity_at": datetime.utcnow()}},
        )

    async def list_room_ids_with_memory(self, limit: int | None = None) -> list[str]:
        room_ids: list[str] = []
        skip = 0
        batch_size = 500
        while True:
            remaining = None if limit is None else max(0, limit - len(room_ids))
            if remaining == 0:
                break
            current_limit = min(batch_size, remaining) if remaining else batch_size
            docs = await self._memories.find(
                {},
                projection={"room_id": 1},
                limit=current_limit,
                skip=skip,
                sort=[("room_id", 1)],
            )
            if not docs:
                break
            room_ids.extend(doc["room_id"] for doc in docs if doc.get("room_id"))
            skip += len(docs)
            if len(docs) < current_limit:
                break
        return room_ids

    async def _push_turn(
        self,
        room_id: str,
        turn: dict,
        max_turns: int,
        summary_stub: str,
        max_summary_chars: int,
        *,
        query: dict | None = None,
    ) -> dict | None:
        query = query or {"room_id": room_id}
        update = _push_turn_update(turn, max_turns, summary_stub, max_summary_chars)
        return await self._memories.find_one_and_update(
            query,
            update,
            upsert=False,
            return_document=True,
            projection={"room_id": 1, "memory_id": 1},
        )


class ContentStorageMongoRepository:
    def __init__(
        self,
        *,
        mongo: MongoDAL,
        collection_name: str = "conversation_content",
        index_registry: Any | None = None,
    ) -> None:
        self._content: MongoCollection = mongo.collection(collection_name)
        self._index_registry = index_registry

    async def upsert_full_content(
        self,
        *,
        document_id: str,
        room_id: str,
        turn_id: str,
        content: str,
        content_type: str,
        content_hash: str,
        stored_at: datetime,
        expires_at: datetime | None = None,
        turn_notes: dict | None = None,
    ) -> str:
        set_on_insert = {
            "room_id": room_id,
            "turn_id": turn_id,
            "content": content,
            "content_type": content_type,
            "content_hash": content_hash,
            "stored_at": stored_at,
            "expires_at": expires_at,
        }
        if turn_notes:
            set_on_insert["turn_notes"] = turn_notes
        doc = await self._content.find_one_and_update(
            {"room_id": room_id, "turn_id": turn_id},
            {
                "$set": {"document_id": document_id},
                "$setOnInsert": set_on_insert,
            },
            upsert=True,
            return_document=True,
        )
        if doc and doc.get("document_id"):
            return doc["document_id"]
        return document_id

    async def get_content_by_document_id(self, document_id: str) -> dict | None:
        doc = await self._content.find_one({"document_id": document_id})
        if doc is not None:
            return doc
        return await self._content.find_one_by_stable_or_native_id(
            "document_id", document_id
        )

    async def get_content_by_turn_id(
        self, room_id: str, turn_id: str
    ) -> dict | None:
        return await self._content.find_one({"room_id": room_id, "turn_id": turn_id})

    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool:
        return await self._content.delete_one({"room_id": room_id, "turn_id": turn_id})

    async def delete_content_by_room_id(self, room_id: str) -> int:
        return await self._content.delete_many({"room_id": room_id})

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        pipeline = [
            {"$match": {"room_id": room_id}},
            {
                "$group": {
                    "_id": "$content_type",
                    "count": {"$sum": 1},
                    "total_size": {"$sum": {"$strLenBytes": "$content"}},
                }
            },
        ]
        rows = await self._content.aggregate(pipeline)
        stats = {
            "room_id": room_id,
            "by_type": {},
            "total_documents": 0,
            "total_size_bytes": 0,
        }
        for row in rows:
            content_type = row.get("_id")
            count = row.get("count", 0)
            size = row.get("total_size", 0)
            stats["by_type"][content_type] = {"count": count, "size_bytes": size}
            stats["total_documents"] += count
            stats["total_size_bytes"] += size
        return stats

    async def text_search(
        self, room_id: str, query: str, limit: int = 50
    ) -> list[dict]:
        return await self._content.find(
            {"room_id": room_id, "$text": {"$search": query}},
            projection={
                "score": {"$meta": "textScore"},
                "turn_id": 1,
                "turn_notes": 1,
                "content": 1,
                "content_type": 1,
                "stored_at": 1,
            },
            sort=[("score", {"$meta": "textScore"})],
            limit=limit,
        )

    async def hydrate_turn_notes(
        self, room_id: str, turn_ids: list[str]
    ) -> list[dict]:
        if not turn_ids:
            return []
        return await self._content.find(
            {"room_id": room_id, "turn_id": {"$in": turn_ids}},
            projection={"turn_id": 1, "turn_notes": 1},
            limit=len(turn_ids),
        )


def _sanitize_update(updates: dict) -> dict:
    immutable = {"_id"}
    return {key: value for key, value in updates.items() if key not in immutable}


def _history_contains_turn(doc: dict, turn_id: str) -> bool:
    for path in (
        (doc.get("memory_content") or {}).get("conversation_history") or [],
        doc.get("conversation_history") or [],
    ):
        if any(turn.get("turn_id") == turn_id for turn in path):
            return True
    return False


def _push_turn_update(
    turn: dict,
    max_turns: int,
    summary_stub: str,
    max_summary_chars: int,
) -> list[dict]:
    return [
        {
            "$set": {
                "memory_content.conversation_history": {
                    "$slice": [
                        {
                            "$concatArrays": [
                                {
                                    "$ifNull": [
                                        "$memory_content.conversation_history",
                                        [],
                                    ]
                                },
                                [turn],
                            ]
                        },
                        -max_turns,
                    ]
                },
                "conversation_history": {
                    "$slice": [
                        {"$concatArrays": [{"$ifNull": ["$conversation_history", []]}, [turn]]},
                        -max_turns,
                    ]
                },
                "memory_content.summary": {
                    "$substrCP": [
                        {
                            "$concat": [
                                {"$ifNull": ["$memory_content.summary", ""]},
                                "\n",
                                summary_stub,
                            ]
                        },
                        0,
                        max_summary_chars,
                    ]
                },
                "last_activity_at": "$$NOW",
                "total_messages": {"$add": [{"$ifNull": ["$total_messages", 0]}, 1]},
            }
        }
    ]
