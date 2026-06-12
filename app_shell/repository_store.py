from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from app_shell.repository_parts import (
    AppShellAgentRoomStore,
    AppShellMessageStore,
    AppShellTaskLifecycleStore,
)
from app_shell.repository_parts.parsing import (
    _extract_text_from_artifact_parts,
    _modified_count,
    _mongo_update_succeeded,
    _safe_parse_agent,
    _safe_parse_agent_group,
    _safe_parse_agent_message,
    _safe_parse_chat_context,
    _safe_parse_room,
    _safe_parse_room_memory,
    _safe_parse_user_message,
    _strip_file_urls,
    _strip_unset_task_tracking_fields,
    _task_tracking_matches,
)
from app_shell.repository_parts.task_lifecycle_store import (
    _generate_webhook_token,
    _hash_webhook_token,
    _verify_webhook_token,
)
from common.protocols import (
    AgentRepository,
    MessageRepository,
    MongoDAL,
    RoomRepository,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.agent import Agent
from models.agent_group import AgentGroup
from models.memory import ChatContext, RoomMemory
from models.room import MessageContent, Room, RoomAgentMessage, RoomUserMessage

logger = get_logger(__name__)

__all__ = [
    "AppShellRepositoryStore",
    "_extract_text_from_artifact_parts",
    "_modified_count",
    "_mongo_update_succeeded",
    "_safe_parse_agent",
    "_safe_parse_agent_group",
    "_safe_parse_agent_message",
    "_safe_parse_chat_context",
    "_safe_parse_room",
    "_safe_parse_room_memory",
    "_safe_parse_user_message",
    "_strip_file_urls",
    "_strip_unset_task_tracking_fields",
    "_task_tracking_matches",
]


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
        self._agent_room_part = AppShellAgentRoomStore(
            agent_groups=self._agent_groups,
            agents=self._agents,
            room_repository=self._room_repository,
            agent_repository=self._agent_repository,
        )
        self._message_part = AppShellMessageStore(
            room_agent_messages=self._room_agent_messages,
            room_user_messages=self._room_user_messages,
            message_repository=self._message_repository,
        )
        self._task_lifecycle_part = AppShellTaskLifecycleStore(
            room_agent_messages=self._room_agent_messages,
            room_user_messages=self._room_user_messages,
            cancelled_messages=self._cancelled_messages,
            runs=self._runs,
            message_repository=self._message_repository,
            message_store=self.messages,
        )

    @property
    def agent_room(self) -> AppShellAgentRoomStore:
        return self._agent_room_part

    @property
    def messages(self) -> AppShellMessageStore:
        return self._message_part

    @property
    def tasks(self) -> AppShellTaskLifecycleStore:
        return self._task_lifecycle_part

    def _message_delegate(self) -> AppShellMessageStore:
        part = getattr(self, "_message_part", None)
        if part is not None:
            return part
        return AppShellMessageStore(
            room_agent_messages=getattr(self, "_room_agent_messages", None),
            room_user_messages=getattr(self, "_room_user_messages", None),
            message_repository=getattr(self, "_message_repository", None),
        )

    def _task_delegate(self) -> AppShellTaskLifecycleStore:
        part = getattr(self, "_task_lifecycle_part", None)
        if part is not None:
            return part
        return AppShellTaskLifecycleStore(
            room_agent_messages=getattr(self, "_room_agent_messages", None),
            room_user_messages=getattr(self, "_room_user_messages", None),
            cancelled_messages=getattr(self, "_cancelled_messages", None),
            runs=getattr(self, "_runs", None),
            message_repository=getattr(self, "_message_repository", None),
            message_store=self._message_delegate(),
        )

    async def add_agent_group(self, agent_group: AgentGroup) -> bool:
        return await self.agent_room.add_agent_group(agent_group)

    async def get_agent_groups_by_owner(self, owner_id: str) -> list[AgentGroup]:
        return await self.agent_room.get_agent_groups_by_owner(owner_id)

    async def get_agent_group_by_id(self, group_id: str) -> AgentGroup | None:
        return await self.agent_room.get_agent_group_by_id(group_id)

    async def update_agent_group(self, group_id: str, updates: dict) -> bool:
        return await self.agent_room.update_agent_group(group_id, updates)

    async def delete_agent_group(self, group_id: str) -> bool:
        return await self.agent_room.delete_agent_group(group_id)

    async def get_all_active_agents(self, user_id: str | None = None) -> list[Agent]:
        return await self.agent_room.get_all_active_agents(user_id)

    async def get_agent_name_by_agent_id(self, agent_id: str) -> str | None:
        return await self.agent_room.get_agent_name_by_agent_id(agent_id)

    async def get_agent_by_agent_id(self, agent_id: str) -> Agent | None:
        return await self.agent_room.get_agent_by_agent_id(agent_id)

    async def get_agents_with_conditions(
        self,
        query: dict[str, Any] | None = None,
        limit: int = 0,
    ) -> list[Agent]:
        return await self.agent_room.get_agents_with_conditions(query, limit)

    async def increment_agent_call_count(self, agent_id: str, *, success: bool) -> None:
        return await self.agent_room.increment_agent_call_count(
            agent_id, success=success
        )

    async def get_room_by_room_id(self, room_id: str) -> Room | None:
        return await self.agent_room.get_room_by_room_id(room_id)

    async def get_rooms_by_room_owner_id(self, room_owner_id: str) -> list[Room]:
        return await self.agent_room.get_rooms_by_room_owner_id(room_owner_id)

    async def update_room_by_room_id(self, room_id: str, room: Room) -> bool:
        return await self.agent_room.update_room_by_room_id(room_id, room)

    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> RoomUserMessage | None:
        return await self._message_delegate().get_room_user_message_by_message_id(
            message_id
        )

    async def get_room_user_messages_by_room_id(
        self,
        room_id: str,
    ) -> list[RoomUserMessage]:
        return await self._message_delegate().get_room_user_messages_by_room_id(room_id)

    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RoomAgentMessage | None:
        return await self._message_delegate().get_room_agent_message_by_message_id(
            message_id
        )

    async def get_room_agent_messages_by_room_id(
        self,
        room_id: str,
    ) -> list[RoomAgentMessage]:
        return await self._message_delegate().get_room_agent_messages_by_room_id(
            room_id
        )

    async def get_room_agent_messages_by_related_message_id(
        self, related_message_id: str
    ) -> list[RoomAgentMessage]:
        return await self._message_delegate().get_room_agent_messages_by_related_message_id(
            related_message_id
        )

    async def add_room_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> bool:
        return await self._message_delegate().add_room_agent_message(room_agent_message)

    async def add_room_user_message(self, room_user_message: RoomUserMessage) -> bool:
        return await self._message_delegate().add_room_user_message(room_user_message)

    async def update_room_user_message_by_message_id(
        self, message_id: str, room_user_message: RoomUserMessage
    ) -> bool:
        return await self._message_delegate().update_room_user_message_by_message_id(
            message_id, room_user_message
        )

    async def upsert_room_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> None:
        return await self._message_delegate().upsert_room_agent_message(
            room_agent_message
        )

    async def delete_room_agent_message_by_message_id(self, message_id: str) -> bool:
        return await self._message_delegate().delete_room_agent_message_by_message_id(
            message_id
        )

    async def update_room_agent_message_by_message_id(
        self, message_id: str, room_agent_message: RoomAgentMessage
    ) -> bool:
        return await self._message_delegate().update_room_agent_message_by_message_id(
            message_id, room_agent_message
        )

    async def get_active_runs_by_room_id(self, room_id: str) -> list[dict]:
        return await self._task_delegate().get_active_runs_by_room_id(room_id)

    async def save_continuation_on_message(
        self,
        message_id: str,
        continuation_data: dict,
    ) -> bool:
        return await self._task_delegate().save_continuation_on_message(
            message_id, continuation_data
        )

    async def resolve_client_request_id_for_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> str | None:
        return await self._task_delegate().resolve_client_request_id_for_agent_message(
            room_agent_message
        )

    async def resolve_client_request_id_for_message_id(
        self, message_id: str
    ) -> str | None:
        return await self._task_delegate().resolve_client_request_id_for_message_id(
            message_id
        )

    async def get_task_messages_for_room(
        self, room_id: str, *, limit: int = 50
    ) -> list[RoomAgentMessage]:
        return await self._task_delegate().get_task_messages_for_room(
            room_id, limit=limit
        )

    async def get_pending_task_messages_for_user(
        self, user_id: str, states: list[str]
    ) -> list[RoomAgentMessage]:
        return await self._task_delegate().get_pending_task_messages_for_user(
            user_id, states
        )

    def hash_webhook_token(self, token: str) -> str:
        return _hash_webhook_token(token)

    def verify_webhook_token(self, token: str, stored_hash: str) -> bool:
        return _verify_webhook_token(token, stored_hash)

    def generate_webhook_token(self) -> str:
        return _generate_webhook_token()

    async def check_task_limits(
        self, user_id: str, room_id: str, non_terminal_states: list[str]
    ) -> None:
        task_store = self._task_delegate()
        task_store.MAX_TASKS_PER_USER = self.MAX_TASKS_PER_USER
        task_store.MAX_TASKS_PER_ROOM = self.MAX_TASKS_PER_ROOM
        return await task_store.check_task_limits(user_id, room_id, non_terminal_states)

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
        return await self._task_delegate().enable_task_tracking_on_message(
            message_id=message_id,
            webhook_token_hash=webhook_token_hash,
            agent_url=agent_url,
            task_created_at=task_created_at,
            task_updated_at=task_updated_at,
            task_data=task_data,
        )

    async def update_task_on_message(
        self,
        message_id: str,
        task_data: dict,
        message_text: str | None = None,
    ) -> bool:
        return await self._task_delegate().update_task_on_message(
            message_id, task_data, message_text
        )

    async def update_webhook_token_hash_on_message(
        self, message_id: str, webhook_token_hash: str
    ) -> bool:
        return await self._task_delegate().update_webhook_token_hash_on_message(
            message_id, webhook_token_hash
        )

    async def verify_webhook_token_on_message(self, message_id: str) -> str | None:
        return await self._task_delegate().verify_webhook_token_on_message(message_id)

    async def verify_webhook_token_for_task(
        self, message_id: str, token: str
    ) -> tuple[bool, str]:
        return await self._task_delegate().verify_webhook_token_for_task(
            message_id, token
        )

    async def is_message_cancelled(self, message_id: str) -> bool:
        return await self._task_delegate().is_message_cancelled(message_id)

    async def cancel_message(
        self,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool:
        return await self._task_delegate().cancel_message(
            message_id, requested_by_user_id
        )

    async def get_room_ids_with_non_terminal_runs(self) -> list[str]:
        return await self._task_delegate().get_room_ids_with_non_terminal_runs()

    async def find_stale_non_terminal_runs(
        self,
        stale_minutes: int,
        limit: int = 200,
    ) -> list[dict]:
        return await self._task_delegate().find_stale_non_terminal_runs(
            stale_minutes, limit
        )

    async def get_stale_task_messages(
        self,
        stale_minutes: int,
        non_terminal_states: list[str],
    ) -> list[RoomAgentMessage]:
        return await self._task_delegate().get_stale_task_messages(
            stale_minutes, non_terminal_states
        )

    async def get_expired_task_messages(
        self,
        max_age_hours: int,
        non_terminal_states: list[str],
    ) -> list[RoomAgentMessage]:
        return await self._task_delegate().get_expired_task_messages(
            max_age_hours, non_terminal_states
        )

    async def get_non_tracked_stale_task_messages(
        self,
        max_age_hours: int,
        non_terminal_states: list[str],
    ) -> list[RoomAgentMessage]:
        return await self._task_delegate().get_non_tracked_stale_task_messages(
            max_age_hours, non_terminal_states
        )

    async def get_orphaned_agent_messages(
        self,
        orphan_threshold_minutes: int,
    ) -> list[RoomAgentMessage]:
        return await self._task_delegate().get_orphaned_agent_messages(
            orphan_threshold_minutes
        )

    async def touch_task_message(self, message_id: str) -> bool:
        return await self._task_delegate().touch_task_message(message_id)

    async def get_and_clear_continuation_on_message(
        self, message_id: str
    ) -> dict | None:
        return await self._task_delegate().get_and_clear_continuation_on_message(
            message_id
        )

    async def get_pending_continuation_on_message(self, message_id: str) -> dict | None:
        return await self._task_delegate().get_pending_continuation_on_message(
            message_id
        )

    async def get_and_clear_continuation_on_user_message(
        self, message_id: str
    ) -> dict | None:
        return await self._task_delegate().get_and_clear_continuation_on_user_message(
            message_id
        )

    async def save_continuation_on_user_message(
        self,
        message_id: str,
        continuation_data: dict,
    ) -> bool:
        return await self._task_delegate().save_continuation_on_user_message(
            message_id, continuation_data
        )

    async def get_stuck_supervisor_trajectory_messages(
        self,
        older_than_minutes: int,
        limit: int = 100,
    ) -> list[dict]:
        return await self._task_delegate().get_stuck_supervisor_trajectory_messages(
            older_than_minutes, limit
        )

    async def claim_stuck_supervisor_trajectory(self, message_id: str) -> bool:
        return await self._task_delegate().claim_stuck_supervisor_trajectory(message_id)

    async def get_room_memory_by_room_id(self, room_id: str) -> RoomMemory | None:
        try:
            return _safe_parse_room_memory(
                await self._room_memories.find_one({"room_id": room_id})
            )
        except Exception:
            logger.error("Failed to get room memory", exc_info=True)
            return None

    async def claim_user_message_for_processing(self, message_id: str) -> bool:
        return await self._message_delegate().claim_user_message_for_processing(
            message_id
        )

    async def unclaim_user_message(self, message_id: str) -> bool:
        return await self._message_delegate().unclaim_user_message(message_id)

    async def claim_or_reclaim_user_message(
        self,
        message_id: str,
        stale_threshold: Any,
    ) -> bool:
        return await self._message_delegate().claim_or_reclaim_user_message(
            message_id, stale_threshold
        )

    async def refresh_processing_claim(self, message_id: str) -> bool:
        return await self._message_delegate().refresh_processing_claim(message_id)

    async def turn_exists(self, room_id: str, turn_id: str) -> bool:
        return await self._message_delegate().turn_exists(room_id, turn_id)

    async def cancel_descendants(self, message_id: str) -> int:
        return await self._message_delegate().cancel_descendants(message_id)

    async def cancel_agent_messages_by_ids(self, message_ids: list[str]) -> int:
        return await self._message_delegate().cancel_agent_messages_by_ids(message_ids)

    async def update_room_agent_message_with_new_message_content_by_message_id(
        self, message_id: str, message_content: MessageContent
    ) -> bool:
        return await self._message_delegate().update_room_agent_message_with_new_message_content_by_message_id(
            message_id, message_content
        )

    async def update_last_notified_state(self, message_id: str, state: str) -> bool:
        return await self._message_delegate().update_last_notified_state(
            message_id, state
        )

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

    async def reset_last_notified_state(self, message_id: str) -> bool:
        return await self._message_delegate().reset_last_notified_state(message_id)

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
        return await self._message_delegate().update_task_state_on_message(
            message_id,
            state,
            message_text=message_text,
            artifacts=artifacts,
            task_id=task_id,
            context_id=context_id,
        )

    async def accumulate_artifact_on_message(
        self,
        message_id: str,
        artifact: dict,
        append: bool = False,
    ) -> bool:
        return await self._message_delegate().accumulate_artifact_on_message(
            message_id, artifact, append
        )

    async def update_task_state_on_message_if_not_terminal(
        self,
        message_id: str,
        state: str,
    ) -> bool:
        return (
            await self._message_delegate().update_task_state_on_message_if_not_terminal(
                message_id, state
            )
        )

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
