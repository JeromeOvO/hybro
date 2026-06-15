from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from common.utils.logger import get_logger
from common.utils.time import utcnow

logger = get_logger(__name__)


class AppShellHITLStore:
    def __init__(
        self, *, hitl_requests, room_agent_messages, room_user_messages
    ) -> None:
        self._hitl_requests = hitl_requests
        self._room_agent_messages = room_agent_messages
        self._room_user_messages = room_user_messages

    async def get_pending_hitl_requests_for_message(
        self, user_message_id: str
    ) -> list[dict]:
        try:
            return await self._hitl_requests.find(
                {"user_message_id": user_message_id, "status": "pending"},
                limit=50,
            )
        except Exception:
            logger.error("Failed to get pending HITL requests", exc_info=True)
            return []

    async def create_hitl_request(self, request_data: dict) -> bool:
        try:
            await self._hitl_requests.insert_one(dict(request_data))
            return True
        except Exception:
            logger.error("Failed to create HITL request", exc_info=True)
            return False

    async def get_hitl_request(self, request_id: str) -> dict | None:
        try:
            return await self._hitl_requests.find_one({"request_id": request_id})
        except Exception:
            logger.error("Failed to get HITL request", exc_info=True)
            return None

    async def update_hitl_request(self, request_id: str, **updates) -> bool:
        try:
            return await self._hitl_requests.update_one(
                {"request_id": request_id},
                {"$set": dict(updates)},
            )
        except Exception:
            logger.error("Failed to update HITL request", exc_info=True)
            return False

    async def cas_update_hitl_request(
        self,
        request_id: str,
        expected_status: str,
        **updates,
    ) -> bool:
        try:
            return await self._hitl_requests.update_one(
                {"request_id": request_id, "status": expected_status},
                {"$set": dict(updates)},
            )
        except Exception:
            logger.error("Failed to CAS update HITL request", exc_info=True)
            return False

    async def fenced_update_hitl_request(
        self,
        request_id: str,
        claim_id: str,
        updates: dict | None = None,
        **kw_updates,
    ) -> bool:
        merged = {**(updates or {}), **kw_updates}
        try:
            return await self._hitl_requests.update_one(
                {"request_id": request_id, "claim_id": claim_id},
                {"$set": merged},
            )
        except Exception:
            logger.error("Failed to fenced-update HITL request", exc_info=True)
            return False

    async def claim_hitl_request(self, request_id: str, **updates) -> dict | None:
        try:
            return await self._hitl_requests.find_one_and_update(
                {"request_id": request_id, "status": "pending"},
                {"$set": dict(updates)},
            )
        except Exception:
            logger.error("Failed to claim HITL request", exc_info=True)
            return None

    async def get_pending_hitl_requests(self, room_id: str) -> list[dict]:
        try:
            return await self._hitl_requests.find(
                {"room_id": room_id, "status": "pending"},
                limit=50,
            )
        except Exception:
            logger.error("Failed to get room HITL requests", exc_info=True)
            return []

    async def get_hitl_group_requests(self, group_id: str) -> list[dict]:
        try:
            return await self._hitl_requests.find(
                {"group_id": group_id},
                sort=[("group_index", 1)],
                limit=100,
            )
        except Exception:
            logger.error("Failed to get HITL group requests", exc_info=True)
            return []

    async def count_pending_in_hitl_group(self, group_id: str) -> int:
        try:
            return await self._hitl_requests.count(
                {"group_id": group_id, "status": {"$in": ["pending", "processing"]}},
            )
        except Exception:
            logger.error("Failed to count pending HITL group requests", exc_info=True)
            return -1

    async def claim_hitl_group_routing(
        self,
        group_id: str,
        claim_id: str,
    ) -> bool:
        try:
            return await self._hitl_requests.update_one(
                {
                    "group_id": group_id,
                    "group_index": 0,
                    "group_routing_claim_id": {"$exists": False},
                },
                {
                    "$set": {
                        "group_routing_claim_id": claim_id,
                        "group_routing_claimed_at": utcnow(),
                    }
                },
            )
        except Exception:
            logger.error("Failed to claim HITL group routing", exc_info=True)
            return False

    async def release_hitl_group_routing(
        self,
        group_id: str,
        claim_id: str,
    ) -> bool:
        try:
            return await self._hitl_requests.update_one(
                {"group_id": group_id, "group_routing_claim_id": claim_id},
                {
                    "$unset": {
                        "group_routing_claim_id": "",
                        "group_routing_claimed_at": "",
                    }
                },
            )
        except Exception:
            logger.error("Failed to release HITL group routing", exc_info=True)
            return False

    async def count_hitl_requests_for_message(
        self,
        continuation_message_id: str,
    ) -> int:
        try:
            return await self._hitl_requests.count(
                {
                    "continuation_message_id": continuation_message_id,
                    "status": {"$ne": "canceled"},
                    "$or": [
                        {"group_index": None},
                        {"group_index": {"$exists": False}},
                        {"group_index": 0},
                    ],
                }
            )
        except Exception:
            logger.error("Failed to count HITL requests for message", exc_info=True)
            return 0

    async def update_agent_message_task_state(
        self,
        message_id: str,
        state: str,
    ) -> bool:
        try:
            return await self._room_agent_messages.update_one(
                {"message_id": message_id},
                {"$set": {"message_content.message_task.status.state": state}},
            )
        except Exception:
            logger.error("Failed to update agent message task state", exc_info=True)
            return False

    async def _ensure_message_task_metadata(self, message_id: str) -> None:
        await self._room_agent_messages.update_one(
            {
                "message_id": message_id,
                "message_content.message_task.metadata": None,
            },
            {"$set": {"message_content.message_task.metadata": {}}},
        )

    async def persist_hitl_user_answer(
        self,
        message_id: str,
        user_input: str | None,
    ) -> bool:
        try:
            await self._ensure_message_task_metadata(message_id)
            return await self._room_agent_messages.update_one(
                {"message_id": message_id},
                {
                    "$set": {
                        "message_content.message_task.metadata.user_answer": user_input
                    }
                },
            )
        except Exception:
            logger.error("Failed to persist HITL user answer", exc_info=True)
            return False

    async def persist_hitl_group_metadata(
        self,
        message_id: str,
        *,
        group_id: str,
        group_total: int | None,
        group_index: int | None,
    ) -> bool:
        try:
            await self._ensure_message_task_metadata(message_id)
            updates: dict[str, Any] = {
                "message_content.message_task.metadata.hitl_group_id": group_id,
            }
            if group_total is not None:
                updates["message_content.message_task.metadata.hitl_group_total"] = (
                    group_total
                )
            if group_index is not None:
                updates["message_content.message_task.metadata.hitl_group_index"] = (
                    group_index
                )
            return await self._room_agent_messages.update_one(
                {"message_id": message_id},
                {"$set": updates},
            )
        except Exception:
            logger.error("Failed to persist HITL group metadata", exc_info=True)
            return False

    async def iter_stale_processing_hitl_requests(
        self,
        cutoff: Any,
    ) -> AsyncIterator[dict]:
        try:
            docs = await self._hitl_requests.find(
                {"status": "processing", "responded_at": {"$lt": cutoff}},
            )
        except Exception:
            logger.error(
                "Failed to iterate stale processing HITL requests", exc_info=True
            )
            docs = []
        for doc in docs:
            yield doc

    async def ensure_hitl_indexes(self) -> None:
        try:
            await self._hitl_requests.create_index([("request_id", 1)], unique=True)
            await self._hitl_requests.create_index([("room_id", 1), ("status", 1)])
            await self._hitl_requests.create_index([("expires_at", 1), ("status", 1)])
            await self._hitl_requests.create_index(
                [("user_message_id", 1), ("status", 1)]
            )
            await self._hitl_requests.create_index([("continuation_message_id", 1)])
        except Exception:
            logger.error("Failed to create HITL indexes", exc_info=True)
