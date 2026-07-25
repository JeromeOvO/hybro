from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from common.dto import (
    RuntimeAgentGroup,
    RuntimeAgentRecord,
    RuntimeChatContext,
    RuntimeMessageContent,
    RuntimeRoomAgentMessage,
    RuntimeRoomMemory,
    RuntimeRoomRecord,
    RuntimeRoomUserMessage,
)


@runtime_checkable
class RuntimeAgentRoomStore(Protocol):
    async def add_agent_group(self, agent_group: RuntimeAgentGroup) -> bool: ...
    async def get_agent_groups_by_owner(
        self, owner_id: str
    ) -> list[RuntimeAgentGroup]: ...
    async def get_agent_group_by_id(
        self, group_id: str
    ) -> RuntimeAgentGroup | None: ...
    async def update_agent_group(self, group_id: str, updates: dict) -> bool: ...
    async def delete_agent_group(self, group_id: str) -> bool: ...
    async def get_all_active_agents(
        self, user_id: str | None = None
    ) -> list[RuntimeAgentRecord]: ...
    async def get_agent_name_by_agent_id(self, agent_id: str) -> str | None: ...
    async def get_agent_by_agent_id(
        self, agent_id: str
    ) -> RuntimeAgentRecord | None: ...
    async def get_agents_with_conditions(
        self,
        query: dict[str, Any] | None = None,
        limit: int = 0,
    ) -> list[RuntimeAgentRecord]: ...
    async def increment_agent_call_count(
        self, agent_id: str, *, success: bool
    ) -> None: ...
    async def get_room_by_room_id(self, room_id: str) -> RuntimeRoomRecord | None: ...
    async def get_rooms_by_room_owner_id(
        self, room_owner_id: str
    ) -> list[RuntimeRoomRecord]: ...
    async def update_room_by_room_id(
        self, room_id: str, room: RuntimeRoomRecord
    ) -> bool: ...


@runtime_checkable
class RuntimeMessageStore(Protocol):
    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> RuntimeRoomUserMessage | None: ...
    async def get_room_user_messages_by_room_id(
        self,
        room_id: str,
    ) -> list[RuntimeRoomUserMessage]: ...
    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RuntimeRoomAgentMessage | None: ...
    async def get_room_agent_messages_by_room_id(
        self,
        room_id: str,
    ) -> list[RuntimeRoomAgentMessage]: ...
    async def get_room_agent_messages_by_related_message_id(
        self, related_message_id: str
    ) -> list[RuntimeRoomAgentMessage]: ...
    async def add_room_agent_message(
        self, room_agent_message: RuntimeRoomAgentMessage
    ) -> bool: ...
    async def add_room_user_message(
        self, room_user_message: RuntimeRoomUserMessage
    ) -> bool: ...
    async def update_room_user_message_by_message_id(
        self, message_id: str, room_user_message: RuntimeRoomUserMessage
    ) -> bool: ...
    async def upsert_room_agent_message(
        self, room_agent_message: RuntimeRoomAgentMessage
    ) -> None: ...
    async def delete_room_agent_message_by_message_id(
        self, message_id: str
    ) -> bool: ...
    async def update_room_agent_message_by_message_id(
        self, message_id: str, room_agent_message: RuntimeRoomAgentMessage
    ) -> bool: ...
    async def claim_user_message_for_processing(self, message_id: str) -> bool: ...
    async def unclaim_user_message(self, message_id: str) -> bool: ...
    async def claim_or_reclaim_user_message(
        self,
        message_id: str,
        stale_threshold: Any,
    ) -> bool: ...
    async def refresh_processing_claim(self, message_id: str) -> bool: ...
    async def turn_exists(self, room_id: str, turn_id: str) -> bool: ...
    async def cancel_descendants(self, message_id: str) -> int: ...
    async def cancel_agent_messages_by_ids(self, message_ids: list[str]) -> int: ...
    async def update_room_agent_message_with_new_message_content_by_message_id(
        self, message_id: str, message_content: RuntimeMessageContent
    ) -> bool: ...
    async def update_last_notified_state(self, message_id: str, state: str) -> bool: ...
    async def reset_last_notified_state(self, message_id: str) -> bool: ...
    async def update_task_state_on_message(
        self,
        message_id: str,
        state: str,
        *,
        message_text: str | None = None,
        artifacts: list[dict] | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> tuple[bool, str | None]: ...
    async def update_task_state_on_message_if_not_terminal(
        self,
        message_id: str,
        state: str,
    ) -> bool: ...
    async def accumulate_artifact_on_message(
        self,
        message_id: str,
        artifact: dict,
        append: bool = False,
        update_key: str | None = None,
    ) -> bool: ...


@runtime_checkable
class RuntimeTaskLifecycleStore(Protocol):
    def hash_webhook_token(self, token: str) -> str: ...
    def verify_webhook_token(self, token: str, stored_hash: str) -> bool: ...
    def generate_webhook_token(self) -> str: ...
    async def get_active_runs_by_room_id(self, room_id: str) -> list[dict]: ...
    async def resolve_client_request_id_for_agent_message(
        self, room_agent_message: RuntimeRoomAgentMessage
    ) -> str | None: ...
    async def resolve_client_request_id_for_message_id(
        self, message_id: str
    ) -> str | None: ...
    async def get_task_messages_for_room(
        self, room_id: str, *, limit: int = 50
    ) -> list[RuntimeRoomAgentMessage]: ...
    async def get_pending_task_messages_for_user(
        self, user_id: str, states: list[str]
    ) -> list[RuntimeRoomAgentMessage]: ...
    async def check_task_limits(
        self, user_id: str, room_id: str, non_terminal_states: list[str]
    ) -> None: ...
    async def enable_task_tracking_on_message(
        self,
        *,
        message_id: str,
        webhook_token_hash: str,
        agent_url: str,
        task_created_at: Any,
        task_updated_at: Any,
        task_data: dict,
    ) -> bool: ...
    async def update_task_on_message(
        self,
        message_id: str,
        task_data: dict,
        message_text: str | None = None,
    ) -> bool: ...
    async def update_webhook_token_hash_on_message(
        self, message_id: str, webhook_token_hash: str
    ) -> bool: ...
    async def verify_webhook_token_on_message(self, message_id: str) -> str | None: ...
    async def verify_webhook_token_for_task(
        self, message_id: str, token: str
    ) -> tuple[bool, str]: ...
    async def is_message_cancelled(self, message_id: str) -> bool: ...
    async def cancel_message(
        self,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool: ...
    async def get_room_ids_with_non_terminal_runs(self) -> list[str]: ...
    async def find_stale_non_terminal_runs(
        self,
        stale_minutes: int,
        limit: int = 200,
    ) -> list[dict]: ...
    async def get_stale_task_messages(
        self,
        stale_minutes: int,
        non_terminal_states: list[str],
    ) -> list[RuntimeRoomAgentMessage]: ...
    async def get_expired_task_messages(
        self,
        max_age_hours: int,
        non_terminal_states: list[str],
    ) -> list[RuntimeRoomAgentMessage]: ...
    async def get_non_tracked_stale_task_messages(
        self,
        max_age_hours: int,
        non_terminal_states: list[str],
    ) -> list[RuntimeRoomAgentMessage]: ...
    async def get_orphaned_agent_messages(
        self,
        orphan_threshold_minutes: int,
    ) -> list[RuntimeRoomAgentMessage]: ...
    async def touch_task_message(self, message_id: str) -> bool: ...
    async def get_stuck_supervisor_trajectory_messages(
        self,
        older_than_minutes: int,
        limit: int = 100,
    ) -> list[dict]: ...
    async def claim_stuck_supervisor_trajectory(self, message_id: str) -> bool: ...
    async def get_and_clear_continuation_on_message(
        self, message_id: str
    ) -> dict | None: ...
    async def get_pending_continuation_on_message(
        self, message_id: str
    ) -> dict | None: ...
    async def get_and_clear_continuation_on_user_message(
        self, message_id: str
    ) -> dict | None: ...
    async def save_continuation_on_message(
        self,
        message_id: str,
        continuation_data: dict,
    ) -> bool: ...
    async def save_continuation_on_user_message(
        self,
        message_id: str,
        continuation_data: dict,
    ) -> bool: ...


@runtime_checkable
class RuntimeHITLStore(Protocol):
    async def get_pending_hitl_requests_for_message(
        self, user_message_id: str
    ) -> list[dict]: ...
    async def create_hitl_request(self, request_data: dict) -> bool: ...
    async def get_hitl_request(self, request_id: str) -> dict | None: ...
    async def update_hitl_request(self, request_id: str, **updates) -> bool: ...
    async def cas_update_hitl_request(
        self,
        request_id: str,
        expected_status: str,
        **updates,
    ) -> bool: ...
    async def fenced_update_hitl_request(
        self,
        request_id: str,
        claim_id: str,
        updates: dict | None = None,
        **kw_updates,
    ) -> bool: ...
    async def claim_hitl_request(self, request_id: str, **updates) -> dict | None: ...
    async def get_pending_hitl_requests(self, room_id: str) -> list[dict]: ...
    async def get_hitl_group_requests(self, group_id: str) -> list[dict]: ...
    async def count_pending_in_hitl_group(self, group_id: str) -> int: ...
    async def claim_hitl_group_routing(
        self,
        group_id: str,
        claim_id: str,
    ) -> bool: ...
    async def release_hitl_group_routing(
        self,
        group_id: str,
        claim_id: str,
    ) -> bool: ...
    async def count_hitl_requests_for_message(
        self,
        continuation_message_id: str,
    ) -> int: ...
    async def update_agent_message_task_state(
        self,
        message_id: str,
        state: str,
    ) -> bool: ...
    async def persist_hitl_request_id_on_message(
        self,
        message_id: str,
        request_id: str | None,
    ) -> bool: ...
    async def find_pending_hitl_request_for_agent_message(
        self,
        *,
        room_id: str,
        display_message_id: str | None,
        continuation_message_id: str | None,
        agent_id: str | None,
        a2a_task_id: str | None,
        a2a_context_id: str | None,
    ) -> dict[str, Any] | None: ...
    async def create_or_reuse_pending_hitl_request(
        self,
        request_data: dict[str, Any],
    ) -> tuple[dict[str, Any], bool] | None: ...
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
        group_id: str | None,
        group_total: int | None,
        group_index: int | None,
    ) -> bool: ...
    async def persist_hitl_user_answer(
        self,
        message_id: str,
        user_input: str | None,
    ) -> bool: ...
    async def persist_hitl_group_metadata(
        self,
        message_id: str,
        *,
        group_id: str | None,
        group_total: int | None,
        group_index: int | None,
    ) -> bool: ...
    async def iter_stale_processing_hitl_requests(
        self,
        cutoff: Any,
    ) -> AsyncIterator[dict]: ...
    async def ensure_hitl_indexes(self) -> None: ...


@runtime_checkable
class RuntimeMemoryStore(Protocol):
    async def get_room_memory_by_room_id(
        self, room_id: str
    ) -> RuntimeRoomMemory | None: ...
    async def add_chat_context(self, chat_context: RuntimeChatContext) -> bool: ...
    async def get_chat_context_by_session_id(
        self, session_id: str
    ) -> RuntimeChatContext | None: ...
    async def update_chat_context_by_session_id(
        self, session_id: str, chat_context: RuntimeChatContext
    ) -> bool: ...
    async def delete_chat_context_by_session_id(self, session_id: str) -> bool: ...
    async def increment_user_interactions(self, user_id: str) -> bool: ...
    async def record_agent_call(
        self,
        *,
        agent_id: str,
        success: bool,
        response_time_ms: float = 0.0,
    ) -> bool: ...
    async def update_turn_notes(
        self, room_id: str, turn_id: str, turn_notes: dict
    ) -> bool: ...
