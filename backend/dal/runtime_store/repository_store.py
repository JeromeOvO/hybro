from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from common.dto import (
    RuntimeAgentGroup,
    RuntimeAgentRecord,
    RuntimeMessageContent,
    RuntimeRoomAgentMessage,
    RuntimeRoomMemory,
    RuntimeRoomRecord,
    RuntimeRoomUserMessage,
)
from common.protocols import (
    AgentRepository,
    MessageRepository,
    MongoDAL,
    RoomRepository,
)
from common.utils.logger import get_logger
from dal.runtime_store.contracts import (
    agent_group_to_runtime,
    agent_to_runtime,
    room_agent_message_to_runtime,
    room_memory_to_runtime,
    room_to_runtime,
    room_user_message_to_runtime,
    runtime_agent_groups,
    runtime_agent_messages,
    runtime_agents,
    runtime_rooms,
    runtime_to_agent_group,
    runtime_to_message_content,
    runtime_to_room,
    runtime_to_room_agent_message,
    runtime_to_room_user_message,
    runtime_user_messages,
)
from dal.runtime_store.parts import (
    AgentRoomRuntimeStorePart,
    HITLLifecycleRuntimeStorePart,
    HITLRuntimeStorePart,
    MemoryRuntimeStorePart,
    MessageRuntimeStorePart,
    TaskLifecycleRuntimeStorePart,
)
from dal.runtime_store.parts.parsing import (
    _extract_text_from_artifact_parts,
    _modified_count,
    _mongo_update_succeeded,
    _safe_parse_agent,
    _safe_parse_agent_group,
    _safe_parse_agent_message,
    _safe_parse_room,
    _safe_parse_room_memory,
    _safe_parse_user_message,
    _strip_file_urls,
    _strip_unset_task_tracking_fields,
    _task_tracking_matches,
)
from dal.runtime_store.parts.webhook_tokens import (
    generate_webhook_token,
    hash_webhook_token,
    verify_webhook_token,
)

logger = get_logger(__name__)

__all__ = [
    "RuntimeRepositoryStore",
    "_extract_text_from_artifact_parts",
    "_modified_count",
    "_mongo_update_succeeded",
    "_safe_parse_agent",
    "_safe_parse_agent_group",
    "_safe_parse_agent_message",
    "_safe_parse_room",
    "_safe_parse_room_memory",
    "_safe_parse_user_message",
    "_strip_file_urls",
    "_strip_unset_task_tracking_fields",
    "_task_tracking_matches",
]


class RuntimeRepositoryStore:
    """Compatibility store backed by DAL repositories during runtime-store migration."""

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
        self._agents = mongo.collection("agents")
        self._room_memories = mongo.collection("room_memories")
        self._room_agent_messages = mongo.collection("room_agent_messages")
        self._room_user_messages = mongo.collection("room_user_messages")
        self._cancelled_messages = mongo.collection("cancelled_messages")
        self._hitl_requests = mongo.collection("hitl_requests")
        self._hitl_interactions = mongo.collection("hitl_interactions")
        self._hitl_resume_commands = mongo.collection("hitl_resume_commands")
        self._runs = mongo.collection("runs")
        self._room_repository = room_repository
        self._message_repository = message_repository
        self._agent_repository = agent_repository
        self._agent_room_part = AgentRoomRuntimeStorePart(
            agent_groups=self._agent_groups,
            agents=self._agents,
            room_repository=self._room_repository,
            agent_repository=self._agent_repository,
        )
        self._message_part = MessageRuntimeStorePart(
            room_agent_messages=self._room_agent_messages,
            room_user_messages=self._room_user_messages,
            message_repository=self._message_repository,
        )
        self._task_lifecycle_part = TaskLifecycleRuntimeStorePart(
            room_agent_messages=self._room_agent_messages,
            room_user_messages=self._room_user_messages,
            cancelled_messages=self._cancelled_messages,
            runs=self._runs,
            message_repository=self._message_repository,
            message_store=self.messages,
        )
        self._hitl_part = HITLRuntimeStorePart(
            hitl_requests=self._hitl_requests,
            room_agent_messages=self._room_agent_messages,
            room_user_messages=self._room_user_messages,
        )
        self._hitl_lifecycle_part = HITLLifecycleRuntimeStorePart(
            interactions=self._hitl_interactions,
            resume_commands=self._hitl_resume_commands,
            hitl_requests=self._hitl_requests,
        )
        self._memory_part = MemoryRuntimeStorePart(
            room_memories=self._room_memories,
            room_repository=self._room_repository,
        )

    @property
    def agent_room(self) -> AgentRoomRuntimeStorePart:
        return self._agent_room_part

    @property
    def messages(self) -> MessageRuntimeStorePart:
        return self._message_part

    @property
    def tasks(self) -> TaskLifecycleRuntimeStorePart:
        return self._task_lifecycle_part

    @property
    def hitl(self) -> HITLRuntimeStorePart:
        return self._hitl_part

    @property
    def hitl_lifecycle(self) -> HITLLifecycleRuntimeStorePart:
        return self._hitl_lifecycle_part

    @property
    def memory(self) -> MemoryRuntimeStorePart:
        return self._memory_part

    def _message_delegate(self) -> MessageRuntimeStorePart:
        part = getattr(self, "_message_part", None)
        if part is not None:
            return part
        return MessageRuntimeStorePart(
            room_agent_messages=getattr(self, "_room_agent_messages", None),
            room_user_messages=getattr(self, "_room_user_messages", None),
            message_repository=getattr(self, "_message_repository", None),
        )

    def _task_delegate(self) -> TaskLifecycleRuntimeStorePart:
        part = getattr(self, "_task_lifecycle_part", None)
        if part is not None:
            return part
        return TaskLifecycleRuntimeStorePart(
            room_agent_messages=getattr(self, "_room_agent_messages", None),
            room_user_messages=getattr(self, "_room_user_messages", None),
            cancelled_messages=getattr(self, "_cancelled_messages", None),
            runs=getattr(self, "_runs", None),
            message_repository=getattr(self, "_message_repository", None),
            message_store=self._message_delegate(),
        )

    def _hitl_delegate(self) -> HITLRuntimeStorePart:
        part = getattr(self, "_hitl_part", None)
        if part is not None:
            return part
        return HITLRuntimeStorePart(
            hitl_requests=getattr(self, "_hitl_requests", None),
            room_agent_messages=getattr(self, "_room_agent_messages", None),
            room_user_messages=getattr(self, "_room_user_messages", None),
        )

    def _hitl_lifecycle_delegate(self) -> HITLLifecycleRuntimeStorePart:
        part = getattr(self, "_hitl_lifecycle_part", None)
        if part is not None:
            return part
        return HITLLifecycleRuntimeStorePart(
            interactions=getattr(self, "_hitl_interactions", None),
            resume_commands=getattr(self, "_hitl_resume_commands", None),
            hitl_requests=getattr(self, "_hitl_requests", None),
        )

    def _memory_delegate(self) -> MemoryRuntimeStorePart:
        part = getattr(self, "_memory_part", None)
        if part is not None:
            return part
        return MemoryRuntimeStorePart(
            room_memories=getattr(self, "_room_memories", None),
            room_repository=getattr(self, "_room_repository", None),
        )

    async def add_agent_group(self, agent_group: RuntimeAgentGroup) -> bool:
        return await self.agent_room.add_agent_group(
            runtime_to_agent_group(agent_group)
        )

    async def get_agent_groups_by_owner(self, owner_id: str) -> list[RuntimeAgentGroup]:
        return runtime_agent_groups(
            await self.agent_room.get_agent_groups_by_owner(owner_id)
        )

    async def get_agent_group_by_id(self, group_id: str) -> RuntimeAgentGroup | None:
        agent_group = await self.agent_room.get_agent_group_by_id(group_id)
        return agent_group_to_runtime(agent_group) if agent_group is not None else None

    async def update_agent_group(self, group_id: str, updates: dict) -> bool:
        return await self.agent_room.update_agent_group(group_id, updates)

    async def delete_agent_group(self, group_id: str) -> bool:
        return await self.agent_room.delete_agent_group(group_id)

    async def get_all_active_agents(
        self, user_id: str | None = None
    ) -> list[RuntimeAgentRecord]:
        return runtime_agents(await self.agent_room.get_all_active_agents(user_id))

    async def get_agent_name_by_agent_id(self, agent_id: str) -> str | None:
        return await self.agent_room.get_agent_name_by_agent_id(agent_id)

    async def get_agent_by_agent_id(self, agent_id: str) -> RuntimeAgentRecord | None:
        agent = await self.agent_room.get_agent_by_agent_id(agent_id)
        return agent_to_runtime(agent) if agent is not None else None

    async def get_agents_with_conditions(
        self,
        query: dict[str, Any] | None = None,
        limit: int = 0,
    ) -> list[RuntimeAgentRecord]:
        return runtime_agents(
            await self.agent_room.get_agents_with_conditions(query, limit)
        )

    async def increment_agent_call_count(self, agent_id: str, *, success: bool) -> None:
        return await self.agent_room.increment_agent_call_count(
            agent_id, success=success
        )

    async def get_room_by_room_id(self, room_id: str) -> RuntimeRoomRecord | None:
        room = await self.agent_room.get_room_by_room_id(room_id)
        return room_to_runtime(room) if room is not None else None

    async def get_rooms_by_room_owner_id(
        self, room_owner_id: str
    ) -> list[RuntimeRoomRecord]:
        return runtime_rooms(
            await self.agent_room.get_rooms_by_room_owner_id(room_owner_id)
        )

    async def update_room_by_room_id(
        self, room_id: str, room: RuntimeRoomRecord
    ) -> bool:
        return await self.agent_room.update_room_by_room_id(
            room_id, runtime_to_room(room)
        )

    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> RuntimeRoomUserMessage | None:
        message = await self._message_delegate().get_room_user_message_by_message_id(
            message_id
        )
        return room_user_message_to_runtime(message) if message is not None else None

    async def get_room_user_message_by_message_id_strict(
        self,
        message_id: str,
    ) -> RuntimeRoomUserMessage | None:
        return (
            await self._message_delegate().get_room_user_message_by_message_id_strict(
                message_id
            )
        )

    async def get_room_user_messages_by_room_id(
        self,
        room_id: str,
    ) -> list[RuntimeRoomUserMessage]:
        return runtime_user_messages(
            await self._message_delegate().get_room_user_messages_by_room_id(room_id)
        )

    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RuntimeRoomAgentMessage | None:
        message = await self._message_delegate().get_room_agent_message_by_message_id(
            message_id
        )
        return room_agent_message_to_runtime(message) if message is not None else None

    async def get_room_agent_messages_by_room_id(
        self,
        room_id: str,
    ) -> list[RuntimeRoomAgentMessage]:
        return runtime_agent_messages(
            await self._message_delegate().get_room_agent_messages_by_room_id(room_id)
        )

    async def get_room_agent_messages_by_related_message_id(
        self, related_message_id: str
    ) -> list[RuntimeRoomAgentMessage]:
        return runtime_agent_messages(
            await self._message_delegate().get_room_agent_messages_by_related_message_id(
                related_message_id
            )
        )

    async def get_room_agent_messages_by_related_message_id_strict(
        self,
        related_message_id: str,
    ) -> list[RuntimeRoomAgentMessage]:
        return await self._message_delegate().get_room_agent_messages_by_related_message_id_strict(
            related_message_id
        )

    async def add_room_agent_message(
        self, room_agent_message: RuntimeRoomAgentMessage
    ) -> bool:
        return await self._message_delegate().add_room_agent_message(
            runtime_to_room_agent_message(room_agent_message)
        )

    async def add_room_user_message(
        self, room_user_message: RuntimeRoomUserMessage
    ) -> bool:
        return await self._message_delegate().add_room_user_message(
            runtime_to_room_user_message(room_user_message)
        )

    async def set_turn_completion_kind(
        self, message_id: str, completion_kind: str
    ) -> str:
        return await self._message_delegate().set_turn_completion_kind(
            message_id, completion_kind
        )

    async def set_system_task_terminal_state(
        self, message_id: str, target_state: str, *, event_id: str
    ) -> str:
        return await self._message_delegate().set_system_task_terminal_state(
            message_id, target_state, event_id=event_id
        )

    async def update_room_user_message_by_message_id(
        self, message_id: str, room_user_message: RuntimeRoomUserMessage
    ) -> bool:
        return await self._message_delegate().update_room_user_message_by_message_id(
            message_id, runtime_to_room_user_message(room_user_message)
        )

    async def upsert_room_agent_message(
        self, room_agent_message: RuntimeRoomAgentMessage
    ) -> None:
        return await self._message_delegate().upsert_room_agent_message(
            runtime_to_room_agent_message(room_agent_message)
        )

    async def delete_room_agent_message_by_message_id(self, message_id: str) -> bool:
        return await self._message_delegate().delete_room_agent_message_by_message_id(
            message_id
        )

    async def update_room_agent_message_by_message_id(
        self, message_id: str, room_agent_message: RuntimeRoomAgentMessage
    ) -> bool:
        return await self._message_delegate().update_room_agent_message_by_message_id(
            message_id, runtime_to_room_agent_message(room_agent_message)
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
        self, room_agent_message: RuntimeRoomAgentMessage
    ) -> str | None:
        return await self._task_delegate().resolve_client_request_id_for_agent_message(
            runtime_to_room_agent_message(room_agent_message)
        )

    async def resolve_client_request_id_for_message_id(
        self, message_id: str
    ) -> str | None:
        return await self._task_delegate().resolve_client_request_id_for_message_id(
            message_id
        )

    async def get_task_messages_for_room(
        self, room_id: str, *, limit: int = 50
    ) -> list[RuntimeRoomAgentMessage]:
        return runtime_agent_messages(
            await self._task_delegate().get_task_messages_for_room(room_id, limit=limit)
        )

    async def get_pending_task_messages_for_user(
        self, user_id: str, states: list[str]
    ) -> list[RuntimeRoomAgentMessage]:
        return runtime_agent_messages(
            await self._task_delegate().get_pending_task_messages_for_user(
                user_id, states
            )
        )

    def hash_webhook_token(self, token: str) -> str:
        return hash_webhook_token(token)

    def verify_webhook_token(self, token: str, stored_hash: str) -> bool:
        return verify_webhook_token(token, stored_hash)

    def generate_webhook_token(self) -> str:
        return generate_webhook_token()

    async def check_task_limits(
        self, user_id: str, room_id: str, non_terminal_states: list[str]
    ) -> None:
        return await self._task_delegate().check_task_limits(
            user_id,
            room_id,
            non_terminal_states,
            max_tasks_per_user=self.MAX_TASKS_PER_USER,
            max_tasks_per_room=self.MAX_TASKS_PER_ROOM,
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

    async def is_message_cancelled_strict(self, message_id: str) -> bool:
        return await self._task_delegate().is_message_cancelled_strict(message_id)

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
    ) -> list[RuntimeRoomAgentMessage]:
        return runtime_agent_messages(
            await self._task_delegate().get_stale_task_messages(
                stale_minutes, non_terminal_states
            )
        )

    async def get_expired_task_messages(
        self,
        max_age_hours: int,
        non_terminal_states: list[str],
    ) -> list[RuntimeRoomAgentMessage]:
        return runtime_agent_messages(
            await self._task_delegate().get_expired_task_messages(
                max_age_hours, non_terminal_states
            )
        )

    async def get_non_tracked_stale_task_messages(
        self,
        max_age_hours: int,
        non_terminal_states: list[str],
    ) -> list[RuntimeRoomAgentMessage]:
        return runtime_agent_messages(
            await self._task_delegate().get_non_tracked_stale_task_messages(
                max_age_hours, non_terminal_states
            )
        )

    async def get_orphaned_agent_messages(
        self,
        orphan_threshold_minutes: int,
    ) -> list[RuntimeRoomAgentMessage]:
        return runtime_agent_messages(
            await self._task_delegate().get_orphaned_agent_messages(
                orphan_threshold_minutes
            )
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

    async def get_room_memory_by_room_id(
        self, room_id: str
    ) -> RuntimeRoomMemory | None:
        room_memory = await self._memory_delegate().get_room_memory_by_room_id(room_id)
        return room_memory_to_runtime(room_memory) if room_memory is not None else None

    async def get_pending_hitl_requests_for_message(
        self, user_message_id: str
    ) -> list[dict]:
        return await self._hitl_delegate().get_pending_hitl_requests_for_message(
            user_message_id
        )

    async def get_pending_hitl_requests_for_message_strict(
        self,
        user_message_id: str,
    ) -> list[dict]:
        return await self._hitl_delegate().get_pending_hitl_requests_for_message_strict(
            user_message_id
        )

    async def create_hitl_request(self, request_data: dict) -> bool:
        return await self._hitl_delegate().create_hitl_request(request_data)

    async def get_hitl_request(self, request_id: str) -> dict | None:
        return await self._hitl_delegate().get_hitl_request(request_id)

    async def update_hitl_request(self, request_id: str, **updates) -> bool:
        return await self._hitl_delegate().update_hitl_request(request_id, **updates)

    async def cas_update_hitl_request(
        self,
        request_id: str,
        expected_status: str,
        **updates,
    ) -> bool:
        return await self._hitl_delegate().cas_update_hitl_request(
            request_id, expected_status, **updates
        )

    async def cas_update_hitl_request_strict(
        self,
        request_id: str,
        expected_status: str,
        **updates,
    ) -> bool:
        return await self._hitl_delegate().cas_update_hitl_request_strict(
            request_id,
            expected_status,
            **updates,
        )

    async def fenced_update_hitl_request(
        self,
        request_id: str,
        claim_id: str,
        updates: dict | None = None,
        **kw_updates,
    ) -> bool:
        return await self._hitl_delegate().fenced_update_hitl_request(
            request_id, claim_id, updates, **kw_updates
        )

    async def claim_hitl_request(self, request_id: str, **updates) -> dict | None:
        return await self._hitl_delegate().claim_hitl_request(request_id, **updates)

    async def get_pending_hitl_requests(self, room_id: str) -> list[dict]:
        return await self._hitl_delegate().get_pending_hitl_requests(room_id)

    async def get_pending_hitl_requests_strict(self, room_id: str) -> list[dict]:
        return await self._hitl_delegate().get_pending_hitl_requests_strict(room_id)

    async def count_hitl_requests_for_message(
        self,
        continuation_message_id: str,
    ) -> int:
        return await self._hitl_delegate().count_hitl_requests_for_message(
            continuation_message_id
        )

    async def update_agent_message_task_state(
        self,
        message_id: str,
        state: str,
    ) -> bool:
        return await self._hitl_delegate().update_agent_message_task_state(
            message_id, state
        )

    async def persist_hitl_request_id_on_message(
        self,
        message_id: str,
        request_id: str | None,
    ) -> bool:
        return await self._hitl_delegate().persist_hitl_request_id_on_message(
            message_id, request_id
        )

    async def find_pending_hitl_request_for_agent_message(
        self,
        *,
        room_id: str,
        display_message_id: str | None,
        continuation_message_id: str | None,
        agent_id: str | None,
        a2a_task_id: str | None,
        a2a_context_id: str | None,
    ) -> dict[str, Any] | None:
        return await self._hitl_delegate().find_pending_hitl_request_for_agent_message(
            room_id=room_id,
            display_message_id=display_message_id,
            continuation_message_id=continuation_message_id,
            agent_id=agent_id,
            a2a_task_id=a2a_task_id,
            a2a_context_id=a2a_context_id,
        )

    async def create_or_reuse_pending_hitl_request(
        self,
        request_data: dict[str, Any],
    ) -> tuple[dict[str, Any], bool] | None:
        return await self._hitl_delegate().create_or_reuse_pending_hitl_request(
            request_data
        )

    async def claim_hitl_open_projection(
        self, request_id: str, claim_id: str
    ) -> dict[str, Any] | None:
        return await self._hitl_delegate().claim_hitl_open_projection(
            request_id, claim_id
        )

    async def complete_hitl_open_projection(
        self, request_id: str, claim_id: str
    ) -> bool:
        return await self._hitl_delegate().complete_hitl_open_projection(
            request_id, claim_id
        )

    async def release_hitl_open_projection(
        self, request_id: str, claim_id: str
    ) -> bool:
        return await self._hitl_delegate().release_hitl_open_projection(
            request_id, claim_id
        )

    async def persist_pending_hitl_on_agent_message(
        self,
        message_id: str,
        *,
        request_id: str,
        prompt: str,
        prompt_type: Any,
        choices: list[str] | None,
        a2a_task_id: str | None,
        a2a_context_id: str | None,
        interaction_id: str,
        question_count: int,
        question_index: int,
        expected_request_id: str | None = None,
        projection_at: datetime | None = None,
    ) -> bool:
        return await self._hitl_delegate().persist_pending_hitl_on_agent_message(
            message_id,
            request_id=request_id,
            prompt=prompt,
            prompt_type=prompt_type,
            choices=choices,
            a2a_task_id=a2a_task_id,
            a2a_context_id=a2a_context_id,
            interaction_id=interaction_id,
            question_count=question_count,
            question_index=question_index,
            expected_request_id=expected_request_id,
            projection_at=projection_at,
        )

    async def _ensure_message_task_metadata(self, message_id: str) -> None:
        return await self._hitl_delegate()._ensure_message_task_metadata(message_id)

    async def persist_hitl_user_answer(
        self,
        message_id: str,
        user_input: str | None,
    ) -> bool:
        return await self._hitl_delegate().persist_hitl_user_answer(
            message_id, user_input
        )

    async def persist_hitl_interaction_metadata(
        self,
        message_id: str,
        *,
        interaction_id: str | None,
        question_count: int | None,
        question_index: int | None,
    ) -> bool:
        return await self._hitl_delegate().persist_hitl_interaction_metadata(
            message_id,
            interaction_id=interaction_id,
            question_count=question_count,
            question_index=question_index,
        )

    async def iter_stale_processing_hitl_requests(
        self,
        cutoff: Any,
    ) -> AsyncIterator[dict]:
        async for doc in self._hitl_delegate().iter_stale_processing_hitl_requests(
            cutoff
        ):
            yield doc

    async def ensure_hitl_indexes(self) -> None:
        return await self._hitl_delegate().ensure_hitl_indexes()

    async def update_turn_notes(
        self, room_id: str, turn_id: str, turn_notes: dict
    ) -> bool:
        return await self._memory_delegate().update_turn_notes(
            room_id, turn_id, turn_notes
        )

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

    async def get_stale_claimed_orchestration_messages(
        self,
        orphan_threshold_minutes: int,
        limit: int = 100,
        after_message_id: str | None = None,
    ) -> list[RuntimeRoomUserMessage]:
        return await self._message_delegate().get_stale_claimed_orchestration_messages(
            orphan_threshold_minutes,
            limit,
            after_message_id,
        )

    async def update_orchestration_projection_if_status(
        self,
        message_id: str,
        *,
        expected_status: str,
        status: str,
        clear_processing_claim: bool = False,
    ) -> bool:
        return await self._message_delegate().update_orchestration_projection_if_status(
            message_id,
            expected_status=expected_status,
            status=status,
            clear_processing_claim=clear_processing_claim,
        )

    async def turn_exists(self, room_id: str, turn_id: str) -> bool:
        return await self._message_delegate().turn_exists(room_id, turn_id)

    async def cancel_descendants(self, message_id: str) -> int:
        return await self._message_delegate().cancel_descendants(message_id)

    async def project_descendant_terminal_state(
        self,
        message_id: str,
        *,
        event_id: str,
        target_state: str,
        exclude_message_ids: list[str] | None = None,
    ) -> list[str]:
        return await self._message_delegate().project_descendant_terminal_state(
            message_id,
            event_id=event_id,
            target_state=target_state,
            exclude_message_ids=exclude_message_ids,
        )

    async def cancel_agent_messages_by_ids(self, message_ids: list[str]) -> int:
        return await self._message_delegate().cancel_agent_messages_by_ids(message_ids)

    async def update_room_agent_message_with_new_message_content_by_message_id(
        self, message_id: str, message_content: RuntimeMessageContent
    ) -> bool:
        return await self._message_delegate().update_room_agent_message_with_new_message_content_by_message_id(
            message_id, runtime_to_message_content(message_content)
        )

    async def update_last_notified_state(self, message_id: str, state: str) -> bool:
        return await self._message_delegate().update_last_notified_state(
            message_id, state
        )

    async def reset_last_notified_state(self, message_id: str) -> bool:
        return await self._message_delegate().reset_last_notified_state(message_id)

    async def update_task_state_on_message(
        self,
        message_id: str,
        state: str,
        *,
        message_text: str | None = None,
        artifacts: list[dict] | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        task_metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        return await self._message_delegate().update_task_state_on_message(
            message_id,
            state,
            message_text=message_text,
            artifacts=artifacts,
            task_id=task_id,
            context_id=context_id,
            task_metadata=task_metadata,
        )

    async def terminal_finalization_matches(
        self,
        message_id: str,
        state: str,
        *,
        recovery_source: str,
        recovery_id: str | None,
    ) -> bool:
        return await self._message_delegate().terminal_finalization_matches(
            message_id,
            state,
            recovery_source=recovery_source,
            recovery_id=recovery_id,
        )

    async def accumulate_artifact_on_message(
        self,
        message_id: str,
        artifact: dict,
        append: bool = False,
        update_key: str | None = None,
    ) -> bool:
        return await self._message_delegate().accumulate_artifact_on_message(
            message_id, artifact, append, update_key
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
