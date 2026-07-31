from __future__ import annotations

from datetime import timedelta
from typing import Any

from common.a2a_constants import TERMINAL_STATES
from common.utils.logger import get_logger
from common.utils.time import utcnow
from dal.runtime_store.parts.parsing import (
    _safe_parse_agent_message,
    _task_tracking_matches,
)
from dal.runtime_store.parts.webhook_tokens import (
    generate_webhook_token,
    get_webhook_signing_key,
    hash_webhook_token,
    verify_webhook_token,
)
from models.room import RoomAgentMessage
from models.run import NON_TERMINAL_RUN_STATE_VALUES

logger = get_logger(__name__)


class TaskLifecycleRuntimeStorePart:
    def __init__(
        self,
        *,
        room_agent_messages,
        room_user_messages,
        cancelled_messages,
        runs,
        message_repository,
        message_store,
    ) -> None:
        self._room_agent_messages = room_agent_messages
        self._room_user_messages = room_user_messages
        self._cancelled_messages = cancelled_messages
        self._runs = runs
        self._message_repository = message_repository
        self._messages = message_store

    async def get_active_runs_by_room_id(self, room_id: str) -> list[dict]:
        try:
            return await self._runs.find(
                {
                    "room_id": room_id,
                    "state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)},
                },
                sort=[("updated_at", -1)],
            )
        except Exception:
            logger.error("Failed to get active runs for room", exc_info=True)
            return []

    async def save_continuation_on_message(
        self,
        message_id: str,
        continuation_data: dict,
    ) -> bool:
        try:
            return await self._room_agent_messages.update_one(
                {"message_id": message_id},
                {
                    "$set": {
                        "pending_continuation": continuation_data,
                        "task_updated_at": utcnow(),
                    }
                },
            )
        except Exception:
            logger.error("Failed to save agent-message continuation", exc_info=True)
            return False

    async def resolve_client_request_id_for_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> str | None:
        if room_agent_message.client_request_id:
            return room_agent_message.client_request_id
        visited: set[str] = set()
        cursor = room_agent_message.related_message_id
        for _ in range(12):
            if not cursor or cursor in visited:
                break
            visited.add(cursor)

            user_message = await self._messages.get_room_user_message_by_message_id(
                cursor
            )
            if user_message and user_message.client_request_id:
                return user_message.client_request_id

            parent_agent = await self._messages.get_room_agent_message_by_message_id(
                cursor
            )
            if parent_agent is None:
                break
            if parent_agent.client_request_id:
                return parent_agent.client_request_id
            cursor = parent_agent.related_message_id

        if room_agent_message.turn_id:
            turn_user_message = (
                await self._messages.get_room_user_message_by_message_id(
                    room_agent_message.turn_id
                )
            )
            if turn_user_message and turn_user_message.client_request_id:
                return turn_user_message.client_request_id

        return None

    async def resolve_client_request_id_for_message_id(
        self, message_id: str
    ) -> str | None:
        user_message = await self._messages.get_room_user_message_by_message_id(
            message_id
        )
        if user_message and user_message.client_request_id:
            return user_message.client_request_id

        agent_message = await self._messages.get_room_agent_message_by_message_id(
            message_id
        )
        if agent_message is not None:
            return await self.resolve_client_request_id_for_agent_message(agent_message)

        return None

    async def get_task_messages_for_room(
        self, room_id: str, *, limit: int = 50
    ) -> list[RoomAgentMessage]:
        try:
            docs = await self._message_repository.get_task_messages_for_room(
                room_id,
                limit,
            )
        except Exception:
            logger.error("Failed to get task messages for room", exc_info=True)
            return []
        return [
            msg
            for msg in (
                _safe_parse_agent_message(doc) for doc in docs if doc is not None
            )
            if msg is not None
        ]

    async def get_pending_task_messages_for_user(
        self, user_id: str, states: list[str]
    ) -> list[RoomAgentMessage]:
        try:
            docs = await self._message_repository.get_pending_task_messages_for_user(
                user_id,
                states,
            )
        except Exception:
            logger.error("Failed to get pending task messages", exc_info=True)
            return []
        messages = [_safe_parse_agent_message(doc) for doc in docs if doc is not None]
        return [message for message in messages if message is not None]

    def _get_webhook_signing_key(self) -> bytes:
        return get_webhook_signing_key()

    def hash_webhook_token(self, token: str) -> str:
        return hash_webhook_token(token)

    def verify_webhook_token(self, token: str, stored_hash: str) -> bool:
        return verify_webhook_token(token, stored_hash)

    def generate_webhook_token(self) -> str:
        return generate_webhook_token()

    async def check_task_limits(
        self,
        user_id: str,
        room_id: str,
        non_terminal_states: list[str],
        *,
        max_tasks_per_user: int,
        max_tasks_per_room: int,
    ) -> None:
        user_count = await self._count_agent_messages(
            {
                "user_id": user_id,
                "message_content.message_task.status.state": {
                    "$in": list(non_terminal_states)
                },
                "has_task_tracking": True,
            }
        )
        if user_count >= max_tasks_per_user:
            raise ValueError(
                f"User has too many pending tasks ({user_count}). "
                "Please wait for some to complete."
            )

        room_count = await self._count_agent_messages(
            {
                "room_id": room_id,
                "message_content.message_task.status.state": {
                    "$in": list(non_terminal_states)
                },
                "has_task_tracking": True,
            }
        )
        if room_count >= max_tasks_per_room:
            raise ValueError(
                f"Room has too many pending tasks ({room_count}). "
                "Please wait for some to complete."
            )

    async def enable_task_tracking_on_message(
        self,
        *,
        message_id: str,
        webhook_token_hash: str,
        agent_url: str,
        task_created_at: Any,
        task_updated_at: Any,
        task_data: dict,
    ) -> bool:
        updates = {
            "has_task_tracking": True,
            "webhook_token_hash": webhook_token_hash,
            "agent_url": agent_url,
            "task_created_at": task_created_at,
            "task_updated_at": task_updated_at,
            "message_content.message_task": task_data,
        }
        try:
            updated = await self._message_repository.update_agent_message(
                message_id, updates
            )
            if updated:
                return True
            doc = await self._message_repository.get_agent_message_by_id(message_id)
            return _task_tracking_matches(
                doc,
                webhook_token_hash=webhook_token_hash,
                agent_url=agent_url,
                task_data=task_data,
            )
        except Exception:
            logger.error("Failed to enable task tracking on message", exc_info=True)
            return False

    async def update_task_on_message(
        self,
        message_id: str,
        task_data: dict,
        message_text: str | None = None,
    ) -> bool:
        terminal_values = {state.value for state in TERMINAL_STATES}

        updates: dict[str, Any] = {
            "message_content.message_task": task_data,
            "task_updated_at": utcnow(),
        }
        if message_text is not None:
            updates["message_content.message_text"] = message_text
        try:
            return await self._message_repository.update_agent_message_if_not_terminal(
                message_id,
                updates,
                sorted(terminal_values),
            )
        except Exception:
            logger.error("Failed to update task on message", exc_info=True)
            return False

    async def update_webhook_token_hash_on_message(
        self, message_id: str, webhook_token_hash: str
    ) -> bool:
        try:
            return await self._message_repository.update_agent_message(
                message_id,
                {"webhook_token_hash": webhook_token_hash},
            )
        except Exception:
            logger.error("Failed to update webhook token hash", exc_info=True)
            return False

    async def verify_webhook_token_on_message(self, message_id: str) -> str | None:
        message = await self._messages.get_room_agent_message_by_message_id(message_id)
        if not message or not message.has_task_tracking:
            return None
        return message.webhook_token_hash

    async def verify_webhook_token_for_task(
        self, message_id: str, token: str
    ) -> tuple[bool, str]:
        try:
            stored_hash = await self.verify_webhook_token_on_message(message_id)
            if not stored_hash:
                return False, "task_not_found"
            if not self.verify_webhook_token(token, stored_hash):
                return False, "invalid_token"
            return True, ""
        except Exception:
            logger.error("Failed to verify webhook token", exc_info=True)
            return False, "verification_error"

    async def is_message_cancelled(self, message_id: str) -> bool:
        try:
            reader = getattr(self._message_repository, "is_message_cancelled", None)
            if callable(reader):
                return await reader(message_id)
            return (
                await self._cancelled_messages.find_one({"message_id": message_id})
            ) is not None
        except Exception:
            logger.error("Failed to check message cancellation", exc_info=True)
            return False

    async def is_message_cancelled_strict(self, message_id: str) -> bool:
        reader = getattr(self._message_repository, "is_message_cancelled", None)
        if callable(reader):
            return await reader(message_id)
        return (
            await self._cancelled_messages.find_one({"message_id": message_id})
        ) is not None

    async def list_pending_cancellation_markers(
        self,
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

    async def mark_cancellation_reconciled(self, message_id: str) -> bool:
        try:
            await self._cancelled_messages.update_one(
                {"message_id": message_id},
                {
                    "$set": {
                        "reconciliation_status": "reconciled",
                        "reconciled_at": utcnow(),
                    }
                },
            )
            return True
        except Exception:
            logger.error("Failed to reconcile cancellation marker", exc_info=True)
            return False

    async def cancel_message(
        self,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool:
        try:
            await self._cancelled_messages.update_one(
                {"message_id": message_id},
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
            return True
        except Exception:
            logger.error("Failed to cancel message", exc_info=True)
            return False

    async def get_room_ids_with_non_terminal_runs(self) -> list[str]:
        try:
            ids = await self._runs.distinct(
                "room_id",
                {"state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)}},
            )
            return [str(room_id) for room_id in ids if room_id]
        except Exception:
            logger.error("Failed to get rooms with non-terminal runs", exc_info=True)
            return []

    async def find_stale_non_terminal_runs(
        self,
        stale_minutes: int,
        limit: int = 200,
    ) -> list[dict]:
        try:
            cutoff = utcnow() - timedelta(minutes=stale_minutes)
            return await self._runs.find(
                {
                    "state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)},
                    "updated_at": {"$lt": cutoff},
                },
                sort=[("updated_at", 1)],
                limit=limit,
            )
        except Exception:
            logger.error("Failed to find stale runs", exc_info=True)
            return []

    async def get_stale_task_messages(
        self,
        stale_minutes: int,
        non_terminal_states: list[str],
    ) -> list[RoomAgentMessage]:
        threshold = utcnow() - timedelta(minutes=stale_minutes)
        return await self._find_agent_messages(
            {
                "message_content.message_task.status.state": {
                    "$in": list(non_terminal_states)
                },
                "task_updated_at": {"$lt": threshold},
                "has_task_tracking": True,
            },
            "Failed to get stale task messages",
        )

    async def get_expired_task_messages(
        self,
        max_age_hours: int,
        non_terminal_states: list[str],
    ) -> list[RoomAgentMessage]:
        threshold = utcnow() - timedelta(hours=max_age_hours)
        return await self._find_agent_messages(
            {
                "message_content.message_task.status.state": {
                    "$in": list(non_terminal_states)
                },
                "task_created_at": {"$lt": threshold},
                "has_task_tracking": True,
            },
            "Failed to get expired task messages",
        )

    async def get_non_tracked_stale_task_messages(
        self,
        max_age_hours: int,
        non_terminal_states: list[str],
    ) -> list[RoomAgentMessage]:
        threshold = utcnow() - timedelta(hours=max_age_hours)
        return await self._find_agent_messages(
            {
                "message_content.message_task.status.state": {
                    "$in": list(non_terminal_states)
                },
                "message_created_at": {"$lt": threshold},
                "has_task_tracking": {"$ne": True},
            },
            "Failed to get non-tracked stale task messages",
        )

    async def get_orphaned_agent_messages(
        self,
        orphan_threshold_minutes: int,
    ) -> list[RoomAgentMessage]:
        threshold = utcnow() - timedelta(minutes=orphan_threshold_minutes)
        return await self._find_agent_messages(
            {
                "message_type": "agent",
                "message_created_at": {"$lt": threshold},
                "$and": [
                    {
                        "$or": [
                            {"has_task_tracking": {"$ne": True}},
                            {"has_task_tracking": {"$exists": False}},
                        ]
                    },
                    {
                        "$or": [
                            {"message_content.message_task.status": {"$exists": False}},
                            {"message_content.message_task.status": None},
                        ]
                    },
                ],
            },
            "Failed to get orphaned agent messages",
        )

    async def touch_task_message(self, message_id: str) -> bool:
        try:
            return await self._room_agent_messages.update_one(
                {"message_id": message_id, "has_task_tracking": True},
                {"$set": {"task_updated_at": utcnow()}},
            )
        except Exception:
            logger.error("Failed to touch task message", exc_info=True)
            return False

    async def get_and_clear_continuation_on_message(
        self, message_id: str
    ) -> dict | None:
        try:
            doc = await self._room_agent_messages.find_one_and_update(
                {
                    "message_id": message_id,
                    "pending_continuation": {"$exists": True, "$ne": None},
                },
                {
                    "$set": {
                        "pending_continuation": None,
                        "task_updated_at": utcnow(),
                    }
                },
            )
            return doc.get("pending_continuation") if doc else None
        except Exception:
            logger.error("Failed to get and clear agent continuation", exc_info=True)
            return None

    async def get_pending_continuation_on_message(self, message_id: str) -> dict | None:
        try:
            agent_doc = await self._room_agent_messages.find_one(
                {"message_id": message_id, "pending_continuation": {"$exists": True}},
            )
            if agent_doc and agent_doc.get("pending_continuation"):
                return agent_doc["pending_continuation"]
            user_doc = await self._room_user_messages.find_one(
                {"message_id": message_id, "pending_continuation": {"$exists": True}},
            )
            if user_doc and user_doc.get("pending_continuation"):
                return user_doc["pending_continuation"]
            return None
        except Exception:
            logger.error("Failed to get pending continuation", exc_info=True)
            return None

    async def get_and_clear_continuation_on_user_message(
        self, message_id: str
    ) -> dict | None:
        try:
            doc = await self._room_user_messages.find_one_and_update(
                {"message_id": message_id, "pending_continuation": {"$exists": True}},
                {"$unset": {"pending_continuation": ""}},
            )
            return doc.get("pending_continuation") if doc else None
        except Exception:
            logger.error("Failed to get and clear user continuation", exc_info=True)
            return None

    async def save_continuation_on_user_message(
        self,
        message_id: str,
        continuation_data: dict,
    ) -> bool:
        try:
            updated = await self._room_user_messages.update_one(
                {"message_id": message_id},
                {"$set": {"pending_continuation": continuation_data}},
            )
            if updated:
                return True
            return (
                await self._room_user_messages.find_one({"message_id": message_id})
            ) is not None
        except Exception:
            logger.error("Failed to save user-message continuation", exc_info=True)
            return False

    async def _find_agent_messages(
        self,
        query: dict,
        log_message: str,
    ) -> list[RoomAgentMessage]:
        try:
            docs = await self._room_agent_messages.find(query)
            messages = [
                _safe_parse_agent_message(doc) for doc in docs if doc is not None
            ]
            return [message for message in messages if message is not None]
        except Exception:
            logger.error(log_message, exc_info=True)
            return []

    async def _count_agent_messages(self, query: dict) -> int:
        counter = getattr(self._message_repository, "count_agent_messages", None)
        if callable(counter):
            return await counter(query)
        return 0
