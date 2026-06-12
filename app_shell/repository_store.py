from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

from common.a2a_constants import TERMINAL_STATES, CommonTaskState
from common.config.settings import settings
from common.protocols import (
    AgentRepository,
    MessageRepository,
    MongoDAL,
    RoomRepository,
)
from common.utils.a2a_helpers import (
    sanitize_artifact_parts,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.agent import Agent
from models.agent_group import AgentGroup
from models.memory import ChatContext, RoomMemory
from models.room import MessageContent, Room, RoomAgentMessage, RoomUserMessage
from models.run import NON_TERMINAL_RUN_STATE_VALUES
from models.supervisor import TrajectoryStatus

logger = get_logger(__name__)


class AppShellRepositoryStore:
    """Compatibility store backed by DAL repositories during app-shell migration."""

    MAX_TASKS_PER_USER = 100
    MAX_TASKS_PER_ROOM = 50

    def __init__(
        self,
        *,
        mongo: MongoDAL,
        room_repository: RoomRepository,
        message_repository: MessageRepository,
        agent_repository: AgentRepository,
    ) -> None:
        self._agent_groups = mongo.collection("agent_groups")
        self._chat_contexts = mongo.collection("chat_contexts")
        self._agents = mongo.collection("agents")
        self._user_memories = mongo.collection("user_memories")
        self._agent_memories = mongo.collection("agent_memories")
        self._room_memories = mongo.collection("room_memories")
        self._room_agent_messages = mongo.collection("room_agent_messages")
        self._room_user_messages = mongo.collection("room_user_messages")
        self._cancelled_messages = mongo.collection("cancelled_messages")
        self._hitl_requests = mongo.collection("hitl_requests")
        self._runs = mongo.collection("runs")
        self._room_repository = room_repository
        self._message_repository = message_repository
        self._agent_repository = agent_repository

    async def add_agent_group(self, agent_group: AgentGroup) -> bool:
        try:
            await self._agent_groups.insert_one(agent_group.model_dump(mode="json"))
            return True
        except Exception:
            logger.error("Failed to add agent group", exc_info=True)
            return False

    async def get_agent_groups_by_owner(self, owner_id: str) -> list[AgentGroup]:
        try:
            docs = await self._agent_groups.find({"owner_id": owner_id})
            groups = [_safe_parse_agent_group(doc) for doc in docs if doc is not None]
            return [group for group in groups if group is not None]
        except Exception:
            logger.error("Failed to get agent groups by owner", exc_info=True)
            return []

    async def get_agent_group_by_id(self, group_id: str) -> AgentGroup | None:
        try:
            return _safe_parse_agent_group(
                await self._agent_groups.find_one({"group_id": group_id})
            )
        except Exception:
            logger.error("Failed to get agent group", exc_info=True)
            return None

    async def update_agent_group(self, group_id: str, updates: dict) -> bool:
        try:
            return await self._agent_groups.update_one(
                {"group_id": group_id},
                {"$set": dict(updates)},
            )
        except Exception:
            logger.error("Failed to update agent group", exc_info=True)
            return False

    async def delete_agent_group(self, group_id: str) -> bool:
        try:
            return await self._agent_groups.delete_one({"group_id": group_id})
        except Exception:
            logger.error("Failed to delete agent group", exc_info=True)
            return False

    async def get_all_active_agents(self, user_id: str | None = None) -> list[Agent]:
        try:
            docs = await self._agent_repository.list_visible(
                user_id=user_id,
                active_only=True,
                limit=0,
            )
            agents = [_safe_parse_agent(doc) for doc in docs if doc is not None]
            return [agent for agent in agents if agent is not None]
        except Exception:
            logger.error("Failed to get active agents", exc_info=True)
            return []

    async def get_agent_name_by_agent_id(self, agent_id: str) -> str | None:
        try:
            doc = await self._agent_repository.get_by_id(agent_id)
        except Exception:
            logger.error("Failed to get agent name", exc_info=True)
            return None
        card = (doc or {}).get("agent_card") or {}
        return (
            card.get("name") if isinstance(card, dict) else getattr(card, "name", None)
        )

    async def get_agent_by_agent_id(self, agent_id: str) -> Agent | None:
        try:
            return _safe_parse_agent(await self._agent_repository.get_by_id(agent_id))
        except Exception:
            logger.error("Failed to get agent", exc_info=True)
            return None

    async def get_agents_with_conditions(
        self,
        query: dict[str, Any] | None = None,
        limit: int = 0,
    ) -> list[Agent]:
        try:
            # Legacy room selection accepts arbitrary agent predicates; use the
            # DAL collection directly to preserve that query surface during
            # migration instead of applying AgentRepository visibility filters.
            docs = await self._agents.find(dict(query or {}), limit=limit or None)
            agents = [_safe_parse_agent(doc) for doc in docs if doc is not None]
            return [agent for agent in agents if agent is not None]
        except Exception:
            logger.error("Failed to get agents with conditions", exc_info=True)
            return []

    async def increment_agent_call_count(self, agent_id: str, *, success: bool) -> None:
        try:
            await self._agent_repository.increment_agent_call_count(
                agent_id,
                success=success,
            )
        except Exception:
            logger.error("Failed to increment agent call count", exc_info=True)

    async def get_room_by_room_id(self, room_id: str) -> Room | None:
        try:
            return _safe_parse_room(await self._room_repository.get_by_id(room_id))
        except Exception:
            logger.error("Failed to get room", exc_info=True)
            return None

    async def get_rooms_by_room_owner_id(self, room_owner_id: str) -> list[Room]:
        try:
            docs = await self._room_repository.get_by_owner(room_owner_id)
            rooms = [_safe_parse_room(doc) for doc in docs if doc is not None]
            return [room for room in rooms if room is not None]
        except Exception:
            logger.error("Failed to get rooms by owner", exc_info=True)
            return []

    async def update_room_by_room_id(self, room_id: str, room: Room) -> bool:
        try:
            updates = room.model_dump(exclude_unset=True, mode="json")
            updated = await self._room_repository.update(room_id, updates)
            if updated:
                return True
            # MongoDAL update_one returns False for matched no-op writes because
            # it only exposes modified/upserted state. Legacy database_service
            # treated any matched room update as success, so confirm existence
            # before reporting failure.
            return await self._room_repository.get_by_id(room_id) is not None
        except Exception:
            logger.error("Failed to update room", exc_info=True)
            return False

    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> RoomUserMessage | None:
        try:
            return _safe_parse_user_message(
                await self._message_repository.get_user_message_by_id(message_id)
            )
        except Exception:
            logger.error("Failed to get room user message", exc_info=True)
            return None

    async def get_room_user_messages_by_room_id(
        self,
        room_id: str,
    ) -> list[RoomUserMessage]:
        try:
            docs = await self._message_repository.get_user_messages_for_room(room_id)
            messages = [
                _safe_parse_user_message(doc) for doc in docs if doc is not None
            ]
            return [message for message in messages if message is not None]
        except Exception:
            logger.error("Failed to get room user messages", exc_info=True)
            return []

    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RoomAgentMessage | None:
        try:
            return _safe_parse_agent_message(
                await self._message_repository.get_agent_message_by_id(message_id)
            )
        except Exception:
            logger.error("Failed to get room agent message", exc_info=True)
            return None

    async def get_room_agent_messages_by_room_id(
        self,
        room_id: str,
    ) -> list[RoomAgentMessage]:
        try:
            docs = await self._message_repository.get_agent_messages_for_room(room_id)
            messages = [
                _safe_parse_agent_message(doc) for doc in docs if doc is not None
            ]
            return [message for message in messages if message is not None]
        except Exception:
            logger.error("Failed to get room agent messages", exc_info=True)
            return []

    async def get_room_agent_messages_by_related_message_id(
        self, related_message_id: str
    ) -> list[RoomAgentMessage]:
        try:
            docs = (
                await self._message_repository.get_agent_messages_by_related_message_id(
                    related_message_id
                )
            )
            messages = [
                _safe_parse_agent_message(doc) for doc in docs if doc is not None
            ]
            return [message for message in messages if message is not None]
        except Exception:
            logger.error("Failed to get related agent messages", exc_info=True)
            return []

    async def add_room_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> bool:
        try:
            if room_agent_message.message_id == "":
                room_agent_message.message_id = str(uuid.uuid4())
            await self._message_repository.save_agent_message(
                room_agent_message.model_dump(mode="json")
            )
            return True
        except Exception:
            logger.error("Failed to add room agent message", exc_info=True)
            return False

    async def add_room_user_message(self, room_user_message: RoomUserMessage) -> bool:
        try:
            if room_user_message.message_id == "":
                room_user_message.message_id = str(uuid.uuid4())
            doc = room_user_message.model_dump(mode="json", exclude={"quote"})
            _strip_file_urls(doc)
            return bool(await self._message_repository.save_user_message(doc))
        except Exception:
            logger.error("Failed to add room user message", exc_info=True)
            return False

    async def update_room_user_message_by_message_id(
        self, message_id: str, room_user_message: RoomUserMessage
    ) -> bool:
        try:
            update_data = room_user_message.model_dump(exclude_unset=True, mode="json")
            _strip_file_urls(update_data)
            return await self._message_repository.update_user_message(
                message_id,
                update_data,
            )
        except Exception:
            logger.error("Failed to update room user message", exc_info=True)
            return False

    async def upsert_room_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> None:
        try:
            await self._room_agent_messages.replace_one(
                {"message_id": room_agent_message.message_id},
                room_agent_message.model_dump(mode="json"),
                upsert=True,
            )
        except Exception:
            logger.error("Failed to upsert room agent message", exc_info=True)

    async def delete_room_agent_message_by_message_id(self, message_id: str) -> bool:
        try:
            return await self._room_agent_messages.delete_one({"message_id": message_id})
        except Exception:
            logger.error("Failed to delete room agent message", exc_info=True)
            return False

    async def update_room_agent_message_by_message_id(
        self, message_id: str, room_agent_message: RoomAgentMessage
    ) -> bool:
        try:
            update_data = _strip_unset_task_tracking_fields(
                room_agent_message.model_dump(exclude_unset=True, mode="json")
            )
            return await self._message_repository.update_agent_message(
                message_id,
                update_data,
            )
        except Exception:
            logger.error("Failed to update room agent message", exc_info=True)
            return False

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

    async def get_room_memory_by_room_id(self, room_id: str) -> RoomMemory | None:
        try:
            return _safe_parse_room_memory(
                await self._room_memories.find_one({"room_id": room_id})
            )
        except Exception:
            logger.error("Failed to get room memory", exc_info=True)
            return None

    async def claim_user_message_for_processing(self, message_id: str) -> bool:
        try:
            doc = await self._room_user_messages.find_one_and_update(
                {"message_id": message_id, "processing_claimed_at": None},
                {"$set": {"processing_claimed_at": utcnow()}},
            )
            return doc is not None
        except Exception:
            logger.error("Failed to claim user message", exc_info=True)
            return False

    async def unclaim_user_message(self, message_id: str) -> bool:
        try:
            return await self._room_user_messages.update_one(
                {"message_id": message_id},
                {"$set": {"processing_claimed_at": None}},
            )
        except Exception:
            logger.error("Failed to unclaim user message", exc_info=True)
            return False

    async def claim_or_reclaim_user_message(
        self,
        message_id: str,
        stale_threshold: Any,
    ) -> bool:
        try:
            doc = await self._room_user_messages.find_one_and_update(
                {
                    "message_id": message_id,
                    "$or": [
                        {"processing_claimed_at": None},
                        {"processing_claimed_at": {"$lt": stale_threshold}},
                    ],
                },
                {"$set": {"processing_claimed_at": utcnow()}},
            )
            return doc is not None
        except Exception:
            logger.error("Failed to claim or reclaim user message", exc_info=True)
            return False

    async def refresh_processing_claim(self, message_id: str) -> bool:
        try:
            return await self._room_user_messages.update_one(
                {"message_id": message_id, "processing_claimed_at": {"$ne": None}},
                {"$set": {"processing_claimed_at": utcnow()}},
            )
        except Exception:
            logger.error("Failed to refresh processing claim", exc_info=True)
            return False

    async def turn_exists(self, room_id: str, turn_id: str) -> bool:
        try:
            user = await self._room_user_messages.find_one(
                {"room_id": room_id, "turn_id": turn_id}
            )
            if user is not None:
                return True
            agent = await self._room_agent_messages.find_one(
                {"room_id": room_id, "turn_id": turn_id}
            )
            return agent is not None
        except Exception:
            logger.error("Failed to check turn existence", exc_info=True)
            return False

    async def cancel_descendants(self, message_id: str) -> int:
        terminal_statuses = sorted(state.value for state in TERMINAL_STATES)
        to_visit = [message_id]
        all_descendant_ids: list[str] = []

        while to_visit:
            children = await self._room_agent_messages.find(
                {
                    "related_message_id": {"$in": to_visit},
                    "message_content.message_task": {"$ne": None},
                    "message_content.message_task.status.state": {
                        "$nin": terminal_statuses
                    },
                },
                projection={"message_id": 1},
            )
            child_ids = [
                str(child["message_id"])
                for child in children
                if child.get("message_id") is not None
            ]
            all_descendant_ids.extend(child_ids)
            to_visit = child_ids

        if not all_descendant_ids:
            return 0

        result = await self._room_agent_messages.update_many(
            {"message_id": {"$in": all_descendant_ids}},
            {
                "$set": {
                    "message_content.message_task.status.state": (
                        CommonTaskState.CANCELED.value
                    ),
                }
            },
        )
        return _modified_count(result)

    async def cancel_agent_messages_by_ids(self, message_ids: list[str]) -> int:
        if not message_ids:
            return 0
        terminal_statuses = sorted(state.value for state in TERMINAL_STATES)
        result = await self._room_agent_messages.update_many(
            {
                "message_id": {"$in": list(message_ids)},
                "message_content.message_task": {"$ne": None},
                "message_content.message_task.status.state": {
                    "$nin": terminal_statuses
                },
            },
            {
                "$set": {
                    "message_content.message_task.status.state": (
                        CommonTaskState.CANCELED.value
                    ),
                }
            },
        )
        return _modified_count(result)

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

    async def update_room_agent_message_with_new_message_content_by_message_id(
        self, message_id: str, message_content: MessageContent
    ) -> bool:
        try:
            return await self._message_repository.update_agent_message(
                message_id,
                {"message_content": message_content.model_dump(mode="json")},
            )
        except Exception:
            logger.error("Failed to update room agent message content", exc_info=True)
            return False

    async def update_last_notified_state(self, message_id: str, state: str) -> bool:
        try:
            return await self._room_agent_messages.update_one(
                {"message_id": message_id, "last_notified_state": {"$ne": state}},
                {"$set": {"last_notified_state": state}},
            )
        except Exception:
            logger.error("Failed to update last notified state", exc_info=True)
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

            user_message = await self.get_room_user_message_by_message_id(cursor)
            if user_message and user_message.client_request_id:
                return user_message.client_request_id

            parent_agent = await self.get_room_agent_message_by_message_id(cursor)
            if parent_agent is None:
                break
            if parent_agent.client_request_id:
                return parent_agent.client_request_id
            cursor = parent_agent.related_message_id

        if room_agent_message.turn_id:
            turn_user_message = await self.get_room_user_message_by_message_id(
                room_agent_message.turn_id
            )
            if turn_user_message and turn_user_message.client_request_id:
                return turn_user_message.client_request_id

        return None

    async def resolve_client_request_id_for_message_id(
        self, message_id: str
    ) -> str | None:
        user_message = await self.get_room_user_message_by_message_id(message_id)
        if user_message and user_message.client_request_id:
            return user_message.client_request_id

        agent_message = await self.get_room_agent_message_by_message_id(message_id)
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
        if not settings.webhook_signing_key:
            raise RuntimeError("WEBHOOK_SIGNING_KEY not configured")
        return settings.webhook_signing_key.encode()

    def hash_webhook_token(self, token: str) -> str:
        return hmac.new(
            self._get_webhook_signing_key(),
            token.encode(),
            hashlib.sha256,
        ).hexdigest()

    def verify_webhook_token(self, token: str, stored_hash: str) -> bool:
        return hmac.compare_digest(self.hash_webhook_token(token), stored_hash)

    def generate_webhook_token(self) -> str:
        return secrets.token_urlsafe(32)

    async def check_task_limits(
        self, user_id: str, room_id: str, non_terminal_states: list[str]
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
        if user_count >= self.MAX_TASKS_PER_USER:
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
        if room_count >= self.MAX_TASKS_PER_ROOM:
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
        message = await self.get_room_agent_message_by_message_id(message_id)
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

    async def cancel_message(
        self,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool:
        try:
            await self._cancelled_messages.update_one(
                {"message_id": message_id},
                {
                    "$setOnInsert": {
                        "message_id": message_id,
                        "user_id": requested_by_user_id,
                        "cancelled_at": utcnow(),
                    }
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

    async def reset_last_notified_state(self, message_id: str) -> bool:
        try:
            return await self._room_agent_messages.update_one(
                {"message_id": message_id},
                {"$unset": {"last_notified_state": ""}},
            )
        except Exception:
            logger.error("Failed to reset last notified state", exc_info=True)
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

    async def update_task_state_on_message(
        self,
        message_id: str,
        state: str,
        *,
        message_text: str | None = None,
        artifacts: list[dict] | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> tuple[bool, str | None]:
        resolved_message_text = message_text
        try:
            from common.utils.a2a_helpers import (
                artifacts_to_dicts,
                is_terminal_task_state_value,
                prepare_terminal_agent_content,
            )

            if is_terminal_task_state_value(state):
                if artifacts is None:
                    existing = await self.get_room_agent_message_by_message_id(
                        message_id
                    )
                    task = (
                        existing.message_content.message_task
                        if existing and existing.message_content
                        else None
                    )
                    if task and task.artifacts:
                        artifacts = artifacts_to_dicts(task.artifacts)
                message_text, artifacts, _ = prepare_terminal_agent_content(
                    message_text=message_text,
                    artifacts=artifacts,
                )
                resolved_message_text = message_text

            updates: dict[str, Any] = {
                "message_content.message_task.status.state": state,
                "task_updated_at": utcnow(),
            }
            if message_text is not None:
                updates["message_content.message_text"] = message_text
            if artifacts is not None:
                updates["message_content.message_task.artifacts"] = artifacts
            if task_id is not None:
                updates["message_content.message_task.id"] = task_id
            if context_id is not None:
                updates["message_content.message_task.contextId"] = context_id

            terminal_values = sorted(state.value for state in TERMINAL_STATES)
            updated = (
                await self._message_repository.update_agent_message_if_not_terminal(
                    message_id,
                    updates,
                    terminal_values,
                )
            )
            return updated, resolved_message_text
        except Exception:
            logger.error("Failed to update task state on message", exc_info=True)
            return False, resolved_message_text

    async def accumulate_artifact_on_message(
        self,
        message_id: str,
        artifact: dict,
        append: bool = False,
    ) -> bool:
        """Accumulate A2A artifact chunks with atomic DAL collection updates."""
        try:
            raw_parts = artifact.get("parts", [])
            clean_parts = sanitize_artifact_parts(raw_parts)
            artifact = {**artifact, "parts": clean_parts}

            if append and not clean_parts:
                logger.warning(
                    "All artifact parts dropped by sanitizer; skipping append "
                    "(message_id=%s)",
                    message_id,
                )
                return False

            artifact_id = artifact.get("artifactId") or artifact.get("artifact_id")
            artifact_text = _extract_text_from_artifact_parts(clean_parts)

            base_filter = {
                "message_id": message_id,
                "message_content.message_task.status.state": {
                    "$nin": sorted(state.value for state in TERMINAL_STATES)
                },
            }
            if not artifact_id:
                update: dict[str, Any] = {
                    "$push": {"message_content.message_task.artifacts": artifact},
                    "$set": {
                        "message_content.message_task.status.state": "working",
                        "task_updated_at": utcnow(),
                    },
                }
                if artifact_text:
                    update["$set"]["message_content.message_text"] = artifact_text
                result = await self._room_agent_messages.update_one(base_filter, update)
                return _mongo_update_succeeded(result)

            if append:
                return await self._append_parts_to_artifact(
                    message_id,
                    artifact_id,
                    artifact,
                    artifact_text,
                    base_filter,
                )
            return await self._replace_or_insert_artifact(
                artifact_id,
                artifact,
                artifact_text,
                base_filter,
            )
        except Exception:
            logger.error("Failed to accumulate artifact on message", exc_info=True)
            return False

    @staticmethod
    def _artifact_id_match_expr(artifact_id: str) -> dict[str, Any]:
        return {
            "$or": [
                {"$eq": ["$$art.artifactId", artifact_id]},
                {"$eq": ["$$art.artifact_id", artifact_id]},
            ]
        }

    @classmethod
    def _map_replace_artifact_expr(
        cls, artifact_id: str, artifact: dict
    ) -> dict[str, Any]:
        return {
            "$map": {
                "input": {"$ifNull": ["$message_content.message_task.artifacts", []]},
                "as": "art",
                "in": {
                    "$cond": {
                        "if": cls._artifact_id_match_expr(artifact_id),
                        "then": artifact,
                        "else": "$$art",
                    }
                },
            }
        }

    @classmethod
    def _map_append_parts_expr(
        cls, artifact_id: str, new_parts: list[dict]
    ) -> dict[str, Any]:
        return {
            "$map": {
                "input": {"$ifNull": ["$message_content.message_task.artifacts", []]},
                "as": "art",
                "in": {
                    "$cond": {
                        "if": cls._artifact_id_match_expr(artifact_id),
                        "then": {
                            "$mergeObjects": [
                                "$$art",
                                {
                                    "parts": {
                                        "$concatArrays": [
                                            {"$ifNull": ["$$art.parts", []]},
                                            new_parts,
                                        ]
                                    }
                                },
                            ]
                        },
                        "else": "$$art",
                    }
                },
            }
        }

    async def _append_parts_to_artifact(
        self,
        message_id: str,
        artifact_id: str,
        artifact: dict,
        artifact_text: str,
        base_filter: dict,
    ) -> bool:
        new_parts = artifact.get("parts", [])
        if not new_parts:
            return False

        filter_with_artifact = {
            **base_filter,
            "message_content.message_task.artifacts": {
                "$elemMatch": {
                    "$or": [
                        {"artifactId": artifact_id},
                        {"artifact_id": artifact_id},
                    ]
                }
            },
        }
        set_fields: dict[str, Any] = {
            "message_content.message_task.artifacts": self._map_append_parts_expr(
                artifact_id,
                new_parts,
            ),
            "message_content.message_task.status.state": "working",
            "task_updated_at": utcnow(),
        }
        if artifact_text:
            set_fields["message_content.message_text"] = {
                "$concat": [
                    {"$ifNull": ["$message_content.message_text", ""]},
                    artifact_text,
                ]
            }
        result = await self._room_agent_messages.update_one(
            filter_with_artifact,
            [{"$set": set_fields}],
        )
        if _mongo_update_succeeded(result):
            return True

        logger.warning(
            "append=True for nonexistent artifact %s on message %s, inserting new",
            artifact_id,
            message_id,
        )
        insert_update: dict[str, Any] = {
            "$push": {"message_content.message_task.artifacts": artifact},
            "$set": {
                "message_content.message_task.status.state": "working",
                "task_updated_at": utcnow(),
            },
        }
        if artifact_text:
            insert_update["$set"]["message_content.message_text"] = artifact_text
        result = await self._room_agent_messages.update_one(base_filter, insert_update)
        return _mongo_update_succeeded(result)

    async def _replace_or_insert_artifact(
        self,
        artifact_id: str,
        artifact: dict,
        artifact_text: str,
        base_filter: dict,
    ) -> bool:
        filter_with_artifact = {
            **base_filter,
            "message_content.message_task.artifacts": {
                "$elemMatch": {
                    "$or": [
                        {"artifactId": artifact_id},
                        {"artifact_id": artifact_id},
                    ]
                }
            },
        }
        set_fields: dict[str, Any] = {
            "message_content.message_task.artifacts": self._map_replace_artifact_expr(
                artifact_id,
                artifact,
            ),
            "message_content.message_task.status.state": "working",
            "task_updated_at": utcnow(),
        }
        if artifact_text:
            set_fields["message_content.message_text"] = artifact_text
        result = await self._room_agent_messages.update_one(
            filter_with_artifact,
            [{"$set": set_fields}],
        )
        if _mongo_update_succeeded(result):
            return True

        insert_update: dict[str, Any] = {
            "$push": {"message_content.message_task.artifacts": artifact},
            "$set": {
                "message_content.message_task.status.state": "working",
                "task_updated_at": utcnow(),
            },
        }
        if artifact_text:
            insert_update["$set"]["message_content.message_text"] = artifact_text
        result = await self._room_agent_messages.update_one(base_filter, insert_update)
        return _mongo_update_succeeded(result)

    async def update_task_state_on_message_if_not_terminal(
        self,
        message_id: str,
        state: str,
    ) -> bool:
        try:
            terminal_values = sorted(state.value for state in TERMINAL_STATES)
            return await self._message_repository.update_agent_message_if_not_terminal(
                message_id,
                {
                    "message_content.message_task.status.state": state,
                    "task_updated_at": utcnow(),
                },
                terminal_values,
            )
        except Exception:
            logger.error("Failed to update task state on message", exc_info=True)
            return False

    async def get_stuck_supervisor_trajectory_messages(
        self,
        older_than_minutes: int,
        limit: int = 100,
    ) -> list[dict]:
        try:
            threshold = utcnow() - timedelta(minutes=older_than_minutes)
            return await self._room_user_messages.find(
                {
                    "extend_info.supervisor_trajectory.status": TrajectoryStatus.RUNNING,
                    "extend_info.supervisor": True,
                    "message_created_at": {"$lt": threshold},
                },
                projection={"message_id": 1, "room_id": 1, "_id": 0},
                limit=limit,
            )
        except Exception:
            logger.error(
                "Failed to get stuck supervisor trajectory messages",
                exc_info=True,
            )
            return []

    async def claim_stuck_supervisor_trajectory(self, message_id: str) -> bool:
        try:
            doc = await self._room_user_messages.find_one_and_update(
                {
                    "message_id": message_id,
                    "extend_info.supervisor_trajectory.status": TrajectoryStatus.RUNNING,
                },
                {
                    "$set": {
                        "extend_info.supervisor_trajectory.status": (
                            TrajectoryStatus.RECOVERING
                        ),
                    }
                },
            )
            return doc is not None
        except Exception:
            logger.error("Failed to claim stuck supervisor trajectory", exc_info=True)
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

    async def add_chat_context(self, chat_context: ChatContext) -> bool:
        try:
            if chat_context.memory_id == "":
                chat_context.memory_id = str(uuid.uuid4())
            await self._chat_contexts.insert_one(chat_context.model_dump(mode="json"))
            return True
        except Exception:
            logger.error("Failed to add chat context", exc_info=True)
            return False

    async def get_chat_context_by_session_id(
        self, session_id: str
    ) -> ChatContext | None:
        try:
            return _safe_parse_chat_context(
                await self._chat_contexts.find_one({"session_id": session_id})
            )
        except Exception:
            logger.error("Failed to get chat context", exc_info=True)
            return None

    async def update_chat_context_by_session_id(
        self, session_id: str, chat_context: ChatContext
    ) -> bool:
        try:
            await self._chat_contexts.update_one(
                {"session_id": session_id},
                {"$set": chat_context.model_dump(exclude_unset=True, mode="json")},
            )
            return True
        except Exception:
            logger.error("Failed to update chat context", exc_info=True)
            return False

    async def delete_chat_context_by_session_id(self, session_id: str) -> bool:
        try:
            await self._chat_contexts.delete_one({"session_id": session_id})
            return True
        except Exception:
            logger.error("Failed to delete chat context", exc_info=True)
            return False

    async def increment_user_interactions(self, user_id: str) -> bool:
        now = utcnow()
        try:
            return await self._user_memories.update_one(
                {"user_id": user_id},
                {
                    "$inc": {"total_interactions": 1},
                    "$set": {"last_active_at": now},
                    "$setOnInsert": {"user_id": user_id, "created_at": now},
                },
                upsert=True,
            )
        except Exception:
            logger.error("Failed to increment user interactions", exc_info=True)
            return False

    async def record_agent_call(
        self,
        *,
        agent_id: str,
        success: bool,
        response_time_ms: float = 0.0,
    ) -> bool:
        inc_fields: dict[str, float | int] = {
            "total_calls": 1,
            "total_response_time_ms": response_time_ms,
        }
        if success:
            inc_fields["successful_calls"] = 1
        try:
            return await self._agent_memories.update_one(
                {"agent_id": agent_id},
                {
                    "$inc": inc_fields,
                    "$set": {"last_called_at": utcnow()},
                    "$setOnInsert": {"agent_id": agent_id},
                },
                upsert=True,
            )
        except Exception:
            logger.error("Failed to record agent call", exc_info=True)
            return False

    async def update_turn_notes(
        self, room_id: str, turn_id: str, turn_notes: dict
    ) -> bool:
        updater = getattr(self._room_repository, "update_turn_notes", None)
        if callable(updater):
            try:
                return await updater(room_id, turn_id, turn_notes)
            except Exception:
                logger.error("Failed to update turn notes", exc_info=True)
        return False


def _safe_parse_agent_group(doc: dict | None) -> AgentGroup | None:
    if doc is None:
        return None
    try:
        return AgentGroup.model_validate(doc)
    except Exception:
        logger.warning("Invalid agent group document", exc_info=True)
        return None


def _safe_parse_agent(doc: dict | None) -> Agent | None:
    if doc is None:
        return None
    try:
        return Agent.model_validate(doc)
    except Exception:
        logger.warning("Invalid agent document", exc_info=True)
        return None


def _safe_parse_room(doc: dict | None) -> Room | None:
    if doc is None:
        return None
    try:
        return Room.model_validate(doc)
    except Exception:
        logger.warning("Invalid room document", exc_info=True)
        return None


def _safe_parse_room_memory(doc: dict | None) -> RoomMemory | None:
    if doc is None:
        return None
    try:
        return RoomMemory.model_validate(doc)
    except Exception:
        logger.warning("Invalid room memory document", exc_info=True)
        return None


def _safe_parse_agent_message(doc: dict | None) -> RoomAgentMessage | None:
    if doc is None:
        return None
    try:
        return RoomAgentMessage.model_validate(doc)
    except Exception:
        logger.warning("Invalid room agent message document", exc_info=True)
        return None


def _strip_unset_task_tracking_fields(update_data: dict[str, Any]) -> dict[str, Any]:
    task_tracking_fields = {
        "webhook_token_hash",
        "pending_continuation",
        "last_notified_state",
        "agent_url",
        "task_created_at",
        "task_updated_at",
        "task_content",
    }
    for field in task_tracking_fields:
        if update_data.get(field) is None:
            update_data.pop(field, None)
    if update_data.get("has_task_tracking") is False:
        update_data.pop("has_task_tracking", None)
    return update_data


def _task_tracking_matches(
    doc: dict | None,
    *,
    webhook_token_hash: str,
    agent_url: str,
    task_data: dict,
) -> bool:
    if not doc:
        return False
    message_content = doc.get("message_content") or {}
    return (
        doc.get("has_task_tracking") is True
        and doc.get("webhook_token_hash") == webhook_token_hash
        and doc.get("agent_url") == agent_url
        and message_content.get("message_task") == task_data
    )


def _extract_text_from_artifact_parts(parts: list[dict]) -> str:
    chunks: list[str] = []
    for part in parts:
        root = part.get("root", part)
        if isinstance(root, dict) and isinstance(root.get("text"), str):
            chunks.append(root["text"])
    return "".join(chunks)


def _mongo_update_succeeded(result: Any) -> bool:
    if isinstance(result, bool):
        return result
    modified_count = getattr(result, "modified_count", None)
    upserted_id = getattr(result, "upserted_id", None)
    if modified_count is not None:
        return modified_count > 0 or upserted_id is not None
    return bool(result)


def _modified_count(result: Any) -> int:
    if isinstance(result, bool):
        return int(result)
    if isinstance(result, int):
        return result
    modified_count = getattr(result, "modified_count", None)
    if modified_count is not None:
        return int(modified_count)
    return int(bool(result))


def _safe_parse_user_message(doc: dict | None) -> RoomUserMessage | None:
    if doc is None:
        return None
    try:
        return RoomUserMessage.model_validate(doc)
    except Exception:
        logger.warning("Invalid room user message document", exc_info=True)
        return None


def _strip_file_urls(doc: dict) -> None:
    target = doc.get("$set", doc)
    content = target.get("message_content")
    if not content:
        return
    for attachment in content.get("attachments") or []:
        attachment.pop("file_url", None)


def _safe_parse_chat_context(doc: dict | None) -> ChatContext | None:
    if doc is None:
        return None
    try:
        return ChatContext.model_validate(doc)
    except Exception:
        logger.warning("Invalid chat context document", exc_info=True)
        return None
