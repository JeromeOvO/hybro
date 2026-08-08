from __future__ import annotations

from typing import Any

from pymongo.errors import DuplicateKeyError

from common.utils.logger import get_logger
from common.utils.time import utcnow
from execution.cancellation.ports import CancellationMarkerRepositoryPort

logger = get_logger(__name__)


class MongoCancellationMarkerRepository(CancellationMarkerRepositoryPort):
    """Mongo adapter for Execution-owned durable cancellation markers."""

    def __init__(self, cancelled_messages) -> None:
        self._cancelled_messages = cancelled_messages

    async def request(
        self,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool:
        try:
            existing = await self._cancelled_messages.find_one(
                {"message_id": message_id}
            )
            if existing is not None:
                await self._cancelled_messages.update_many(
                    {"message_id": message_id},
                    {"$set": {"reconciliation_status": "pending"}},
                )
                return True

            try:
                await self._cancelled_messages.update_one(
                    {"_id": f"cancellation:{message_id}"},
                    {
                        "$set": {"reconciliation_status": "pending"},
                        "$setOnInsert": {
                            "message_id": message_id,
                            "user_id": requested_by_user_id,
                            "cancelled_at": utcnow(),
                        },
                    },
                    upsert=True,
                )
            except DuplicateKeyError:
                # Another current binary won the deterministic _id insert after
                # our initial lookup. Its marker is authoritative and pending.
                await self._cancelled_messages.update_many(
                    {"message_id": message_id},
                    {"$set": {"reconciliation_status": "pending"}},
                )
            return True
        except Exception:
            logger.error("Failed to cancel message", exc_info=True)
            return False

    async def list_pending(
        self,
        *,
        limit: int = 100,
        after_message_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "message_id": {"$type": "string"},
            "reconciliation_status": "pending",
        }
        if after_message_id is not None:
            query["message_id"]["$gt"] = after_message_id
        return await self._cancelled_messages.find(
            query,
            projection={"_id": 0},
            sort=[("message_id", 1)],
            limit=limit,
        )

    async def mark_reconciled(self, message_id: str) -> bool:
        try:
            updated = await self._cancelled_messages.update_many(
                {"message_id": message_id},
                {
                    "$set": {
                        "reconciliation_status": "reconciled",
                        "reconciled_at": utcnow(),
                    }
                },
            )
            return updated > 0
        except Exception:
            logger.error("Failed to reconcile cancellation marker", exc_info=True)
            return False


__all__ = ["MongoCancellationMarkerRepository"]
