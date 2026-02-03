"""
A2A Task Service

This service manages long-running A2A tasks in MongoDB.
It provides task creation, updates, webhook token verification, and stale task detection.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from a2a.types import Task
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection

from common.utils.logger import get_logger
from services.a2a_constants import NON_TERMINAL_STATES

logger = get_logger(__name__)


class A2ATaskService:
    """
    Service for managing long-running A2A tasks.

    This service handles:
    - Task creation with webhook token generation
    - Task updates from webhooks or polling
    - Webhook token verification (HMAC-based)
    - Stale and expired task detection
    - Task quota enforcement
    """

    # Configurable limits
    MAX_TASKS_PER_USER = 100  # Max concurrent non-terminal tasks per user
    MAX_TASKS_PER_ROOM = 50  # Max concurrent non-terminal tasks per room

    def __init__(
        self,
        collection: AsyncIOMotorCollection,
        webhook_signing_key: str,
    ):
        """
        Initialize the A2A Task Service.

        Args:
            collection: MongoDB collection for a2a_tasks
            webhook_signing_key: Secret key for HMAC token hashing
        """
        self.collection = collection
        self.webhook_signing_key = webhook_signing_key.encode()

    def _hash_token(self, token: str) -> str:
        """Hash webhook token for storage (never store plaintext)."""
        return hmac.new(
            self.webhook_signing_key, token.encode(), hashlib.sha256
        ).hexdigest()

    def _verify_token(self, token: str, stored_hash: str) -> bool:
        """Verify token against stored hash (constant-time comparison)."""
        computed_hash = self._hash_token(token)
        return hmac.compare_digest(computed_hash, stored_hash)

    async def check_task_limits(self, user_id: str, room_id: str) -> None:
        """
        Check if user/room can create more tasks.

        Args:
            user_id: The user ID
            room_id: The room ID

        Raises:
            ValueError: If limits exceeded
        """
        # Convert TaskState enums to strings for MongoDB query
        non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]

        user_count = await self.collection.count_documents(
            {
                "user_id": user_id,
                "task.status.state": {"$in": non_terminal_state_values},
            }
        )
        if user_count >= self.MAX_TASKS_PER_USER:
            raise ValueError(
                f"User has too many pending tasks ({user_count}). "
                "Please wait for some to complete."
            )

        room_count = await self.collection.count_documents(
            {
                "room_id": room_id,
                "task.status.state": {"$in": non_terminal_state_values},
            }
        )
        if room_count >= self.MAX_TASKS_PER_ROOM:
            raise ValueError(
                f"Room has too many pending tasks ({room_count}). "
                "Please wait for some to complete."
            )

    async def create_task(
        self,
        room_id: str,
        user_id: str,
        agent_url: str,
        task: Task,
        agent_name: str | None = None,
        agent_id: str | None = None,
        related_message_id: str | None = None,
    ) -> tuple[str, str]:
        """
        Create new task record.

        Args:
            room_id: Room this task belongs to
            user_id: User who initiated the task
            agent_url: Agent URL (for fallback polling)
            task: A2A Task object
            agent_name: Optional agent name for display
            agent_id: Optional agent ID for frontend rendering
            related_message_id: Optional room user message ID that initiated the task

        Returns:
            Tuple of (internal_id, webhook_token).
            Token is returned once for sending to agent.

        Raises:
            ValueError: If task limits exceeded
        """
        # Check limits before creating
        await self.check_task_limits(user_id, room_id)

        # Generate token, store only hash
        webhook_token = secrets.token_urlsafe(32)

        doc = {
            "room_id": room_id,
            "user_id": user_id,
            "agent_url": agent_url,
            "agent_name": agent_name,
            "agent_id": agent_id,
            "related_message_id": related_message_id,
            "webhook_token_hash": self._hash_token(webhook_token),
            "last_notified_state": None,
            "task": task.model_dump(mode="json"),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        result = await self.collection.insert_one(doc)
        internal_id = str(result.inserted_id)
        logger.info(f"Created A2A task {internal_id} for room {room_id}")
        return internal_id, webhook_token

    async def update_task(self, internal_id: str, task: Task) -> bool:
        """
        Update task from webhook or polling.

        Args:
            internal_id: Our internal task ID (MongoDB ObjectId as string)
            task: Updated A2A Task object

        Returns:
            True if updated, False if not found
        """
        try:
            result = await self.collection.update_one(
                {"_id": ObjectId(internal_id)},
                {
                    "$set": {
                        "task": task.model_dump(mode="json"),
                        "updated_at": datetime.now(UTC),
                    }
                },
            )
            if result.modified_count > 0:
                logger.debug(f"Updated A2A task {internal_id}")
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Failed to update task {internal_id}: {e}")
            return False

    async def update_notified_state(self, internal_id: str, state: str) -> bool:
        """
        Mark that we've sent SSE for this state.

        Args:
            internal_id: Our internal task ID
            state: The state we're notifying about

        Returns:
            True if this is a new notification (state changed)
        """
        try:
            result = await self.collection.update_one(
                {
                    "_id": ObjectId(internal_id),
                    "last_notified_state": {"$ne": state},  # Only update if different
                },
                {"$set": {"last_notified_state": state}},
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Failed to update notified state for {internal_id}: {e}")
            return False

    async def get_task(self, internal_id: str) -> dict[str, Any] | None:
        """
        Get task document by internal ID.

        Args:
            internal_id: Our internal task ID

        Returns:
            Task document with parsed Task object, or None if not found
        """
        try:
            doc = await self.collection.find_one({"_id": ObjectId(internal_id)})
            if doc:
                doc["internal_id"] = str(doc.pop("_id"))
                doc["task"] = Task.model_validate(doc["task"])
            return doc
        except Exception as e:
            logger.error(f"Failed to get task {internal_id}: {e}")
            return None

    async def verify_webhook_token(self, internal_id: str, token: str) -> bool:
        """
        Verify webhook token for a task.

        Args:
            internal_id: Our internal task ID
            token: Token from Authorization header

        Returns:
            True if token is valid
        """
        try:
            doc = await self.collection.find_one(
                {"_id": ObjectId(internal_id)}, {"webhook_token_hash": 1}
            )
            if not doc or not doc.get("webhook_token_hash"):
                return False
            return self._verify_token(token, doc["webhook_token_hash"])
        except Exception as e:
            logger.error(f"Failed to verify webhook token for {internal_id}: {e}")
            return False

    async def get_tasks_for_room(
        self, room_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """
        Get tasks for a room, newest first.

        Args:
            room_id: The room ID
            limit: Maximum number of tasks to return

        Returns:
            List of task documents
        """
        cursor = (
            self.collection.find(
                {"room_id": room_id},
                {"webhook_token_hash": 0},  # Don't expose token hash
            )
            .sort("created_at", -1)
            .limit(limit)
        )

        tasks = []
        async for doc in cursor:
            doc["internal_id"] = str(doc.pop("_id"))
            doc["task"] = Task.model_validate(doc["task"])
            tasks.append(doc)
        return tasks

    async def get_stale_tasks(self, stale_minutes: int = 10) -> list[dict[str, Any]]:
        """
        Get tasks that haven't been updated recently (includes interactive states).

        Args:
            stale_minutes: Minutes since last update to consider stale

        Returns:
            List of stale task documents
        """
        threshold = datetime.now(UTC) - timedelta(minutes=stale_minutes)
        non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]

        cursor = self.collection.find(
            {
                "task.status.state": {"$in": non_terminal_state_values},
                "updated_at": {"$lt": threshold},
            }
        )

        tasks = []
        async for doc in cursor:
            doc["internal_id"] = str(doc.pop("_id"))
            doc["task"] = Task.model_validate(doc["task"])
            tasks.append(doc)

        logger.debug(f"Found {len(tasks)} stale tasks (>{stale_minutes}min old)")
        return tasks

    async def get_expired_tasks(self, max_age_hours: int = 4) -> list[dict[str, Any]]:
        """
        Get tasks that have been non-terminal for too long (auto-fail candidates).

        Args:
            max_age_hours: Hours since creation to consider expired

        Returns:
            List of expired task documents
        """
        threshold = datetime.now(UTC) - timedelta(hours=max_age_hours)
        non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]

        cursor = self.collection.find(
            {
                "task.status.state": {"$in": non_terminal_state_values},
                "created_at": {"$lt": threshold},
            }
        )

        tasks = []
        async for doc in cursor:
            doc["internal_id"] = str(doc.pop("_id"))
            doc["task"] = Task.model_validate(doc["task"])
            tasks.append(doc)

        logger.debug(f"Found {len(tasks)} expired tasks (>{max_age_hours}h old)")
        return tasks

    async def touch_task(self, internal_id: str) -> None:
        """
        Update timestamp without changing task (for stale detection).

        Args:
            internal_id: Our internal task ID
        """
        try:
            await self.collection.update_one(
                {"_id": ObjectId(internal_id)},
                {"$set": {"updated_at": datetime.now(UTC)}},
            )
        except Exception as e:
            logger.error(f"Failed to touch task {internal_id}: {e}")

    async def get_pending_tasks_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """
        Get all non-terminal tasks for a user.

        Args:
            user_id: The user ID

        Returns:
            List of pending task documents
        """
        non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]

        cursor = self.collection.find(
            {
                "user_id": user_id,
                "task.status.state": {"$in": non_terminal_state_values},
            },
            {"webhook_token_hash": 0},
        ).sort("created_at", -1)

        tasks = []
        async for doc in cursor:
            doc["internal_id"] = str(doc.pop("_id"))
            doc["task"] = Task.model_validate(doc["task"])
            tasks.append(doc)
        return tasks


# Singleton instance will be created in main.py after MongoDB connection
a2a_task_service: A2ATaskService | None = None


def get_a2a_task_service() -> A2ATaskService:
    """Get the A2A task service singleton."""
    if a2a_task_service is None:
        raise RuntimeError(
            "A2A Task Service not initialized. Call init_a2a_task_service() first."
        )
    return a2a_task_service


def init_a2a_task_service(
    collection: AsyncIOMotorCollection,
    webhook_signing_key: str,
) -> A2ATaskService:
    """
    Initialize the A2A task service singleton.

    Args:
        collection: MongoDB collection for a2a_tasks
        webhook_signing_key: Secret key for HMAC token hashing

    Returns:
        The initialized A2ATaskService instance
    """
    global a2a_task_service
    a2a_task_service = A2ATaskService(collection, webhook_signing_key)
    logger.info("A2A Task Service initialized")
    return a2a_task_service
