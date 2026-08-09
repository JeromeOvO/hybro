from __future__ import annotations

from datetime import datetime
from typing import Any

from common.protocols import MongoCollection, MongoDAL
from common.utils.logger import get_logger
from common.utils.time import utcnow

logger = get_logger(__name__)


class MemoryMongoRepository:
    def __init__(
        self,
        *,
        mongo: MongoDAL,
        collection_name: str = "room_memories",
    ) -> None:
        self._memories = mongo.collection(collection_name)

    async def get_room_memory(self, room_id: str) -> dict | None:
        return await self._memories.find_one({"room_id": room_id})

    async def upsert_room_memory(self, room_id: str, memory: dict) -> None:
        memory = _canonicalize_memory_document(memory)
        set_fields = _sanitize_update(memory)
        insert_doc = dict(memory)
        insert_doc["room_id"] = room_id
        set_on_insert = {
            key: value for key, value in insert_doc.items() if key not in set_fields
        }
        await self._memories.update_one(
            {"room_id": room_id},
            {
                "$set": set_fields,
                "$setOnInsert": set_on_insert,
            },
            upsert=True,
        )

    async def delete_room_memory(self, room_id: str) -> bool:
        return await self._memories.delete_one({"room_id": room_id})

    async def create_room_memory(self, memory: dict) -> str:
        memory_id = memory.get("memory_id")
        if not memory_id:
            raise ValueError("memory_id is required")
        await self._memories.insert_one(_canonicalize_memory_document(memory))
        return str(memory_id)

    async def ensure_room_memory(self, room_id: str, defaults: dict) -> dict:
        defaults = _canonicalize_memory_document(defaults)
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

    async def update_room_memory_by_room_id(self, room_id: str, updates: dict) -> bool:
        doc = await self._memories.find_one_and_update(
            {"room_id": room_id},
            {"$set": _sanitize_update(updates)},
            return_document=True,
        )
        return doc is not None

    async def update_room_memory_by_memory_id(
        self, memory_id: str, updates: dict
    ) -> bool:
        doc = await self._memories.find_one_and_update(
            {"memory_id": memory_id},
            {"$set": _sanitize_update(updates)},
            return_document=True,
        )
        return doc is not None

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
        if max_turns <= 0:
            return False, False
        max_summary_chars = max(max_summary_chars, 10)
        doc = await self._push_turn(
            room_id, turn, max_turns, summary_stub, max_summary_chars
        )
        matched = doc is not None
        # The append pipeline always changes a matched room by adding the new turn.
        # That keeps the legacy modified/matched tuple semantics equivalent here.
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
        if max_turns <= 0:
            return False, False, False
        max_summary_chars = max(max_summary_chars, 10)
        query = {
            "room_id": room_id,
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
        existing = await self.get_room_memory(room_id)
        if not existing:
            return False, False, False
        if _history_contains_turn(existing, turn_id):
            return False, True, True
        raise RuntimeError(f"Could not determine projection state for turn {turn_id}")

    async def update_turn_notes(
        self, room_id: str, turn_id: str, turn_notes: dict
    ) -> bool:
        return await self._memories.update_one(
            {"room_id": room_id, "conversation_history.turn_id": turn_id},
            {"$set": {"conversation_history.$[turn].turn_notes": turn_notes}},
            array_filters=[{"turn.turn_id": turn_id}],
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
            update["$push"] = {"room_facts": {"$each": new_facts, "$slice": -max_facts}}
        return await self._memories.update_one({"room_id": room_id}, update)

    async def compact_turns_bulk(
        self, room_id: str, compacted_turns: list[dict]
    ) -> bool:
        if not compacted_turns:
            return True
        turn_ids = [entry["turn_id"] for entry in compacted_turns]
        full_turn_match = {
            "$elemMatch": {
                "turn_id": {"$in": turn_ids},
                "representation": "full",
            }
        }
        try:
            return await self._memories.update_one(
                {
                    "room_id": room_id,
                    "conversation_history": full_turn_match,
                },
                _compact_turns_pipeline(compacted_turns),
            )
        except Exception as exc:
            if isinstance(exc, TypeError | AttributeError | ValueError):
                raise
            logger.exception(
                "Failed to compact turns atomically",
                extra={
                    "room_id": room_id,
                    "turn_count": len(compacted_turns),
                    "error_type": exc.__class__.__name__,
                },
            )
            return False

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
        update = _push_turn_update(turn, max_turns, max_summary_chars)
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
        turn_timestamp: datetime | str | None = None,
        expires_at: datetime | None = None,
        turn_notes: dict | None = None,
    ) -> str:
        fields = {
            "document_id": document_id,
            "room_id": room_id,
            "turn_id": turn_id,
            "content": content,
            "content_type": content_type,
            "content_hash": content_hash,
            "stored_at": stored_at,
            "turn_timestamp": turn_timestamp,
            "expires_at": expires_at,
            "turn_notes": turn_notes,
        }
        doc = await self._content.find_one_and_update(
            {"room_id": room_id, "turn_id": turn_id},
            {"$set": fields},
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

    async def get_content_by_turn_id(self, room_id: str, turn_id: str) -> dict | None:
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
        self,
        room_id: str,
        query: str,
        limit: int = 50,
        skip: int = 0,
    ) -> list[dict]:
        return await self._content.find(
            {
                "room_id": room_id,
                "$text": {"$search": query},
                **_unexpired_content_query(),
            },
            projection={
                "score": {"$meta": "textScore"},
                "turn_id": 1,
                "turn_notes": 1,
                "content_type": 1,
                "stored_at": 1,
                "turn_timestamp": 1,
                "expires_at": 1,
            },
            sort=[
                ("score", {"$meta": "textScore"}),
                ("turn_timestamp", -1),
                ("stored_at", -1),
                ("turn_id", 1),
            ],
            limit=limit,
            skip=skip,
        )

    async def scan_text_search(
        self, room_id: str, query: str, limit: int
    ) -> list[dict]:
        """Read a bounded set of lightweight keyword candidates.

        Full content remains excluded and is hydrated separately in bounded
        batches by the search service.
        """
        return await self._content.find(
            {
                "room_id": room_id,
                "$text": {"$search": query},
                **_unexpired_content_query(),
            },
            projection={
                "score": {"$meta": "textScore"},
                "turn_id": 1,
                "turn_notes": 1,
                "content_type": 1,
                "stored_at": 1,
                "turn_timestamp": 1,
                "expires_at": 1,
            },
            sort=[
                ("score", {"$meta": "textScore"}),
                ("turn_timestamp", -1),
                ("stored_at", -1),
                ("turn_id", 1),
            ],
            limit=max(1, int(limit)),
        )

    async def hydrate_turn_content(
        self, room_id: str, turn_ids: list[str]
    ) -> list[dict]:
        if not turn_ids:
            return []
        return await self._content.find(
            {
                "room_id": room_id,
                "turn_id": {"$in": turn_ids},
                **_unexpired_content_query(),
            },
            projection={
                "turn_id": 1,
                "turn_notes": 1,
                "content": 1,
                "content_type": 1,
                "turn_timestamp": 1,
                "stored_at": 1,
                "expires_at": 1,
            },
            limit=len(turn_ids),
        )


def _canonicalize_memory_document(memory: dict) -> dict:
    doc = dict(memory)
    memory_content = doc.get("memory_content")
    if isinstance(memory_content, dict):
        memory_content = dict(memory_content)
        memory_content.pop("conversation_history", None)
        doc["memory_content"] = memory_content
    doc.pop("memory_content.conversation_history", None)
    return doc


def _sanitize_update(updates: dict) -> dict:
    immutable = {"_id", "room_id", "memory_id"}
    canonical = _canonicalize_memory_document(updates)
    return {key: value for key, value in canonical.items() if key not in immutable}


def _unexpired_content_query() -> dict:
    return {
        "$or": [
            {"expires_at": {"$exists": False}},
            {"expires_at": None},
            {"expires_at": {"$gt": utcnow()}},
        ]
    }


def _history_contains_turn(doc: dict, turn_id: str) -> bool:
    history = doc.get("conversation_history") or []
    return any(
        isinstance(turn, dict) and turn.get("turn_id") == turn_id for turn in history
    )


def _push_turn_update(
    turn: dict,
    max_turns: int,
    max_summary_chars: int,
) -> list[dict]:
    canonical_history = _canonical_history_expression()
    return [
        {
            "$set": {
                "conversation_history": {"$concatArrays": [canonical_history, [turn]]},
                "memory_content.summary": _summary_append_evicted_turn_expression(
                    max_turns=max_turns,
                    max_summary_chars=max_summary_chars,
                ),
                "last_activity_at": "$$NOW",
                "total_messages": {"$add": [{"$ifNull": ["$total_messages", 0]}, 1]},
            }
        }
    ]


def _canonical_history_expression() -> dict:
    return {
        "$cond": [
            {"$isArray": "$conversation_history"},
            "$conversation_history",
            [],
        ]
    }


def _summary_append_evicted_turn_expression(
    *,
    max_turns: int,
    max_summary_chars: int,
) -> dict:
    history = _canonical_history_expression()
    addition = {
        "$cond": [
            {"$gte": [{"$size": history}, max_turns]},
            {
                "$let": {
                    "vars": {
                        "evicted": {
                            "$arrayElemAt": [
                                history,
                                {"$subtract": [{"$size": history}, max_turns]},
                            ]
                        }
                    },
                    "in": _turn_summary_preview_expression("$$evicted"),
                }
            },
            "",
        ]
    }
    return {
        "$let": {
            "vars": {
                "existing": {"$ifNull": ["$memory_content.summary", ""]},
                "addition": addition,
            },
            "in": {
                "$let": {
                    "vars": {
                        "concatenated": {
                            "$cond": [
                                {"$eq": ["$$addition", ""]},
                                "$$existing",
                                {
                                    "$cond": [
                                        {"$eq": ["$$existing", ""]},
                                        "$$addition",
                                        {
                                            "$concat": [
                                                "$$existing",
                                                "\n",
                                                "$$addition",
                                            ]
                                        },
                                    ]
                                },
                            ]
                        }
                    },
                    "in": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$strLenCP": "$$concatenated"},
                                    max_summary_chars,
                                ]
                            },
                            {
                                "$concat": [
                                    "...",
                                    {
                                        "$substrCP": [
                                            "$$concatenated",
                                            {
                                                "$subtract": [
                                                    {"$strLenCP": "$$concatenated"},
                                                    max_summary_chars - 3,
                                                ]
                                            },
                                            max_summary_chars - 3,
                                        ]
                                    },
                                ]
                            },
                            "$$concatenated",
                        ]
                    },
                }
            },
        }
    }


def _turn_summary_preview_expression(turn: str) -> dict:
    role_label = {
        "$switch": {
            "branches": [
                {"case": {"$eq": [f"{turn}.role", "user"]}, "then": "User"},
                {
                    "case": {"$eq": [f"{turn}.role", "agent"]},
                    "then": {"$ifNull": [f"{turn}.agent_name", "Agent"]},
                },
                {
                    "case": {"$eq": [f"{turn}.role", "supervisor"]},
                    "then": "Supervisor",
                },
            ],
            "default": {"$ifNull": [f"{turn}.role", "Unknown"]},
        }
    }
    content = {
        "$cond": [
            {"$eq": [{"$type": f"{turn}.content"}, "string"]},
            f"{turn}.content",
            {
                "$cond": [
                    {"$eq": [{"$type": f"{turn}.brief_summary"}, "string"]},
                    f"{turn}.brief_summary",
                    "[compact turn]",
                ]
            },
        ]
    }
    return {
        "$concat": [
            "[",
            role_label,
            "] ",
            {"$substrCP": [content, 0, 200]},
            "...",
        ]
    }


def _compact_turns_pipeline(compacted_turns: list[dict]) -> list[dict]:
    turn_ids = [entry["turn_id"] for entry in compacted_turns]
    compacted_history = _compact_history_expression(
        "$conversation_history", compacted_turns, turn_ids
    )
    return [
        {
            "$set": {
                "conversation_history": compacted_history,
                "last_activity_at": "$$NOW",
                "total_compactions": {
                    "$add": [{"$ifNull": ["$total_compactions", 0]}, 1]
                },
            }
        }
    ]


def _compact_history_expression(
    history_path: str, compacted_turns: list[dict], turn_ids: list[str]
) -> dict:
    mapped = {
        "$map": {
            "input": history_path,
            "as": "turn",
            "in": {
                "$cond": [
                    {
                        "$and": [
                            {"$in": ["$$turn.turn_id", turn_ids]},
                            {"$eq": ["$$turn.representation", "full"]},
                        ]
                    },
                    {
                        "$mergeObjects": [
                            "$$turn",
                            {
                                "representation": "compact",
                                "content": None,
                                "content_ref": _switch_for_field(
                                    compacted_turns, "content_ref"
                                ),
                                "estimated_tokens_compact": _switch_for_field(
                                    compacted_turns,
                                    "estimated_tokens_compact",
                                    default=20,
                                ),
                                "brief_summary": _switch_for_field(
                                    compacted_turns,
                                    "brief_summary",
                                ),
                            },
                        ]
                    },
                    "$$turn",
                ]
            },
        }
    }
    return {
        "$cond": [
            {"$isArray": history_path},
            mapped,
            "$$REMOVE",
        ]
    }


def _switch_for_field(
    compacted_turns: list[dict], field: str, default: Any | None = None
) -> dict:
    return {
        "$switch": {
            "branches": [
                {
                    "case": {"$eq": ["$$turn.turn_id", entry["turn_id"]]},
                    "then": entry.get(field, default),
                }
                for entry in compacted_turns
            ],
            "default": f"$$turn.{field}",
        }
    }
