from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from common.dto import HITLRequestEvent, HITLResolvedEvent, RunInfo
from common.types import AgentCard
from execution.run_lifecycle_outcome import RunLifecycleWriteOutcome
from models.agent import Agent
from models.agent_network import AgentMessageRequest, AgentMessageResponse
from models.room import (
    Room,
    RoomAgentMessage,
)
from models.run import RunState

ProcessingStatusLike = str | Enum


class DirectAgentMessenger(Protocol):
    async def send(self, request: AgentMessageRequest) -> AgentMessageResponse: ...


class TaskFactory(Protocol):
    def __call__(
        self,
        coro: Awaitable[Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]: ...


RunEventEnabled = Callable[[], bool]
ProcessingStatusEmitter = Callable[..., Awaitable[dict[str, Any] | None]]


class HITLPersistencePort(Protocol):
    async def count_hitl_requests_for_message(self, message_id: str) -> int: ...
    async def create_hitl_request(self, doc: dict[str, Any]) -> bool: ...
    async def claim_hitl_request(
        self,
        request_id: str,
        *,
        status: str,
        claim_id: str | None,
        user_input: str,
        responded_at: Any,
        responded_by_user_id: str,
        answer_digest: str | None = None,
        answered_at: Any | None = None,
    ) -> dict[str, Any] | None: ...
    async def get_hitl_request(self, request_id: str) -> dict[str, Any] | None: ...
    async def update_hitl_request(self, request_id: str, **updates: Any) -> bool: ...
    async def fenced_update_hitl_request(
        self,
        request_id: str,
        claim_id: str,
        *update_docs: dict[str, Any],
        **updates: Any,
    ) -> bool: ...
    async def get_pending_hitl_requests(self, room_id: str) -> list[dict[str, Any]]: ...
    async def get_pending_hitl_requests_strict(
        self, room_id: str
    ) -> list[dict[str, Any]]: ...
    async def get_pending_hitl_requests_for_message(
        self, message_id: str
    ) -> list[dict[str, Any]]: ...
    async def update_agent_message_task_state(
        self, message_id: str, state: str
    ) -> None: ...
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
    async def claim_hitl_open_projection(
        self, request_id: str, claim_id: str
    ) -> dict[str, Any] | None: ...
    async def complete_hitl_open_projection(
        self, request_id: str, claim_id: str
    ) -> bool: ...
    async def release_hitl_open_projection(
        self, request_id: str, claim_id: str
    ) -> bool: ...
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
    ) -> bool: ...
    async def persist_hitl_user_answer(
        self, message_id: str, user_input: str | None
    ) -> None: ...
    async def persist_hitl_interaction_metadata(
        self,
        message_id: str,
        *,
        interaction_id: str | None,
        question_count: int | None,
        question_index: int | None,
    ) -> None: ...
    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> Any | None: ...
    async def get_pending_continuation_on_message(
        self, message_id: str
    ) -> dict[str, Any] | None: ...
    async def save_continuation_on_user_message(
        self, message_id: str, continuation: dict[str, Any]
    ) -> bool: ...
    async def get_and_clear_continuation_on_message(
        self, message_id: str
    ) -> dict[str, Any] | None: ...
    async def get_and_clear_continuation_on_user_message(
        self, message_id: str
    ) -> dict[str, Any] | None: ...
    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> Any | None: ...
    async def resolve_client_request_id_for_message_id(
        self, message_id: str
    ) -> str | None: ...
    async def reset_last_notified_state(self, message_id: str) -> None: ...
    async def iter_stale_processing_hitl_requests(
        self, cutoff: Any
    ) -> AsyncIterator[dict[str, Any]]: ...
    async def cas_update_hitl_request(
        self,
        request_id: str,
        *,
        expected_status: str,
        **updates: Any,
    ) -> bool: ...
    async def cas_update_hitl_request_strict(
        self,
        request_id: str,
        *,
        expected_status: str,
        **updates: Any,
    ) -> bool: ...


class HITLLifecyclePersistencePort(Protocol):
    async def materialize_interaction(
        self, interaction_data: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def attach_interaction_request(
        self,
        interaction_id: str,
        *,
        request_id: str,
        required: bool,
        expires_at: Any,
        question_index: int,
    ) -> dict[str, Any] | None: ...
    async def get_interaction_strict(
        self, interaction_id: str
    ) -> dict[str, Any] | None: ...
    async def get_interaction_for_request_strict(
        self, request_id: str
    ) -> dict[str, Any] | None: ...
    async def iter_materializing_interactions(
        self, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]: ...
    async def record_interaction_answer(
        self,
        interaction_id: str,
        *,
        request_id: str,
        answer_digest: str,
    ) -> dict[str, Any] | None: ...
    async def claim_interaction_application(
        self,
        interaction_id: str,
        *,
        claim_id: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None: ...
    async def renew_interaction_application(
        self,
        interaction_id: str,
        *,
        claim_id: str,
        lease_seconds: int,
    ) -> bool: ...
    async def resume_uncertain_interaction(
        self, interaction_id: str, *, claim_id: str
    ) -> dict[str, Any] | None: ...
    async def claim_run_answer_projection(
        self,
        interaction_id: str,
        *,
        application_revision: int,
        claim_id: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None: ...
    async def renew_run_answer_projection(
        self,
        interaction_id: str,
        *,
        claim_id: str,
        lease_seconds: int,
    ) -> bool: ...
    async def mark_run_answer_projection(
        self,
        interaction_id: str,
        *,
        claim_id: str,
        status: str,
        error: str | None = None,
    ) -> dict[str, Any] | None: ...
    async def mark_interaction_application_state(
        self,
        interaction_id: str,
        *,
        claim_id: str,
        status: str,
        error: str | None = None,
    ) -> dict[str, Any] | None: ...
    async def terminalize_interaction(
        self,
        interaction_id: str,
        *,
        expected_statuses: list[str],
        status: str,
        reason: str,
        member_status: str | None = None,
        owning_run_terminal_status: str | None = None,
    ) -> dict[str, Any] | None: ...
    async def mark_interaction_terminal_reconciled(
        self, interaction_id: str, *, version: int
    ) -> bool: ...
    async def create_resume_command(
        self, command_data: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def get_resume_command_for_interaction_strict(
        self, interaction_id: str, application_revision: int
    ) -> dict[str, Any] | None: ...
    async def claim_resume_command(
        self,
        command_id: str,
        *,
        claim_id: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None: ...
    async def get_resume_command_strict(
        self, command_id: str
    ) -> dict[str, Any] | None: ...
    async def renew_resume_command(
        self,
        command_id: str,
        *,
        claim_id: str,
        lease_seconds: int,
    ) -> bool: ...
    async def reclaim_stale_resume_command(
        self,
        command_id: str,
        *,
        observed_claim_id: str | None,
        observed_version: int,
        observed_lease_expires_at: Any,
        now: Any,
        status: str,
        error_code: str,
        error_message: str,
        retry_after_seconds: int | None = None,
    ) -> dict[str, Any] | None: ...
    async def mark_resume_command_state(
        self,
        command_id: str,
        *,
        claim_id: str | None,
        expected_statuses: list[str],
        status: str,
        response_snapshot: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> dict[str, Any] | None: ...
    async def mark_resume_command_aggregate_applied(self, command_id: str) -> bool: ...
    def iter_due_interactions(
        self, now: Any, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]: ...
    def iter_stale_applications(
        self, now: Any, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]: ...
    def iter_due_resume_commands(
        self, now: Any, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]: ...
    def iter_active_interactions(
        self, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]: ...
    def iter_unreconciled_terminal_interactions(
        self, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]: ...
    def iter_unreconciled_terminal_requests(
        self, *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]: ...
    async def ensure_hitl_lifecycle_indexes(self) -> None: ...


class HITLTerminalLifecyclePort(Protocol):
    async def terminalize_owning_run(
        self,
        request: Any,
        *,
        terminal_status: str,
        reason: str,
    ) -> None: ...


class HITLDeliveryPort(Protocol):
    async def emit(self, event: HITLRequestEvent | HITLResolvedEvent) -> None: ...


class A2ATransportPort(Protocol):
    def has_streaming_capability(self, *, agent_card: AgentCard) -> bool: ...

    def send_message_streaming(
        self,
        agent_card: AgentCard,
        message: Any,
        *,
        agent_id: str | None = None,
    ) -> AsyncIterator[Any]: ...

    async def send_message_sync(
        self,
        *,
        agent_card: AgentCard,
        message: Any,
        agent_id: str | None = None,
    ) -> Any: ...

    async def send_message_to_tracked_agent(
        self,
        *,
        agent_card: AgentCard,
        message: Any,
        message_id: str,
        webhook_token: str,
        context_id: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def create_task_for_tracking(
        self,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        prepared_message: Any,
        *,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> dict[str, Any]: ...

    async def cancel_remote_task(
        self,
        agent_card: AgentCard,
        remote_task_id: str,
    ) -> None: ...

    def has_push_notification_capability(self, agent_card: AgentCard) -> bool: ...


class A2ATaskTrackingStorePort(Protocol):
    async def check_task_limits(
        self,
        user_id: str,
        room_id: str,
        non_terminal_state_values: list[str],
    ) -> None: ...

    def generate_webhook_token(self) -> str: ...
    def hash_webhook_token(self, token: str) -> str: ...

    async def enable_task_tracking_on_message(
        self,
        *,
        message_id: str,
        webhook_token_hash: str,
        agent_url: str,
        task_created_at: Any,
        task_updated_at: Any,
        task_data: dict[str, Any],
    ) -> bool: ...

    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RoomAgentMessage | None: ...

    async def update_webhook_token_hash_on_message(
        self,
        message_id: str,
        webhook_token_hash: str,
    ) -> bool: ...

    async def get_agent_by_agent_id(self, agent_id: str) -> Agent | None: ...

    async def get_hitl_request(self, request_id: str) -> dict[str, Any] | None: ...

    async def update_task_on_message(
        self,
        message_id: str,
        task_data: dict[str, Any],
        message_text: str | None = None,
    ) -> bool: ...


class NotificationServicePort(Protocol):
    async def send_task_update(
        self,
        *,
        room_id: str,
        message_id: str | None,
        status: ProcessingStatusLike,
        agent_card: AgentCard | None = None,
        agent_name: str | None = None,
        agent_id: str | None = None,
        created_at: str | None = None,
        content: str | None = None,
        error: str | None = None,
        requires_input: bool | None = None,
        requires_auth: bool | None = None,
        status_message: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        related_message_id: str | None = None,
        parts: list[dict[str, Any]] | None = None,
        client_request_id: str | None = None,
    ) -> None: ...


class TaskNotificationStorePort(Protocol):
    async def update_last_notified_state(self, message_id: str, state: str) -> bool: ...

    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RoomAgentMessage | None: ...

    async def update_room_agent_message_by_message_id(
        self, message_id: str, room_agent_message: Any
    ) -> bool: ...

    async def get_room_by_room_id(self, room_id: str) -> Room | None: ...

    async def resolve_client_request_id_for_agent_message(
        self, room_agent_message: Any
    ) -> str | None: ...


class ExecutionDeliveryPort(Protocol):
    async def send_task_submitted(
        self,
        room_id: str,
        message_id: str,
        task_id: str,
        agent_name: str,
        agent_id: str | None = None,
        status: ProcessingStatusLike = "working",
        related_message_id: str | None = None,
        created_at: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        client_request_id: str | None = None,
    ) -> None: ...

    async def send_task_update(
        self,
        room_id: str,
        message_id: str,
        status: ProcessingStatusLike,
        content: str | None = None,
        error: str | None = None,
        requires_input: bool = False,
        requires_auth: bool = False,
        status_message: str | None = None,
        agent_name: str | None = None,
        agent_id: str | None = None,
        related_message_id: str | None = None,
        created_at: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        parts: list[dict[str, Any]] | None = None,
        client_request_id: str | None = None,
        delivery_id: str | None = None,
    ) -> bool | None: ...


class RunReadPort(Protocol):
    async def get_run(self, run_id: str) -> RunInfo | None: ...
    async def get_run_strict(self, run_id: str) -> RunInfo | None: ...
    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]: ...
    async def get_latest_runs_for_rooms(
        self, room_ids: list[str]
    ) -> dict[str, RunInfo]: ...


class HITLMessageCancellationPort(Protocol):
    async def cancel_requests_for_message(self, message_id: str) -> None: ...


class ClientRequestIdResolver(Protocol):
    async def resolve_client_request_id(
        self,
        message_id: str | None,
        provided_client_request_id: str | None,
    ) -> str | None: ...


class RunLifecyclePort(Protocol):
    async def write_processing_status(
        self,
        room_id: str,
        status: ProcessingStatusLike,
        message_id: str | None,
        *,
        client_request_id: str | None = None,
        details: dict[str, Any] | None = None,
        error_message: str | None = None,
        terminal_projection: dict[str, Any] | None = None,
    ) -> RunLifecycleWriteOutcome: ...

    async def record_processing_status(
        self,
        room_id: str,
        status: ProcessingStatusLike,
        message_id: str | None,
        *,
        client_request_id: str | None = None,
        details: dict[str, Any] | None = None,
        error_message: str | None = None,
        terminal_projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...

    async def project_run_state(
        self,
        *,
        room_id: str,
        run_id: str,
        trigger_message_id: str,
        target_state: RunState,
        terminal_reason: str | None,
        causation_id: str,
        client_request_id: str | None = None,
        terminal_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...

    async def heal_diverged_runs(self, limit: int = 500) -> int: ...
