from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import Enum
from typing import Any, Protocol

from common.dto import HITLRequestEvent, HITLResolvedEvent, RunInfo
from common.types import AgentCard
from common.utils.cancellation import CancellationToken
from models.memory import RoomMemory
from models.request import RoomCenterAgentMessageRequest
from models.response import RoomCenterAgentMessageResponse, RoomCenterMemoryResponse
from models.room import RoomAgentMessage

ProcessingStatusLike = str | Enum


class TaskFactory(Protocol):
    def __call__(
        self,
        coro: Awaitable[Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]: ...


RunEventEnabled = Callable[[], bool]
RunDualWriteEnabled = Callable[[], bool]


class HITLCoordinator(Protocol):
    async def request_input(
        self,
        room_id: str,
        user_message_id: str,
        source: str,
        prompt: str,
        **kwargs: Any,
    ) -> Any | None: ...

    async def cancel_request(self, request_id: str, room_id: str) -> None: ...


class HITLPersistencePort(Protocol):
    async def count_hitl_requests_for_message(self, message_id: str) -> int: ...
    async def create_hitl_request(self, doc: dict[str, Any]) -> bool: ...
    async def claim_hitl_request(
        self,
        request_id: str,
        *,
        status: str,
        claim_id: str,
        user_input: str,
        responded_at: Any,
        responded_by_user_id: str,
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
    async def count_pending_in_hitl_group(self, group_id: str) -> int: ...
    async def claim_hitl_group_routing(self, group_id: str, claim_id: str) -> bool: ...
    async def release_hitl_group_routing(self, group_id: str, claim_id: str) -> bool: ...
    async def get_hitl_group_requests(self, group_id: str) -> list[dict[str, Any]]: ...
    async def get_pending_hitl_requests(self, room_id: str) -> list[dict[str, Any]]: ...
    async def get_pending_hitl_requests_for_message(
        self, message_id: str
    ) -> list[dict[str, Any]]: ...
    async def update_agent_message_task_state(self, message_id: str, state: str) -> None: ...
    async def persist_hitl_user_answer(
        self, message_id: str, user_input: str | None
    ) -> None: ...
    async def persist_hitl_group_metadata(
        self,
        message_id: str,
        *,
        group_id: str,
        group_total: int | None,
        group_index: int | None,
    ) -> None: ...
    async def get_room_agent_message_by_message_id(self, message_id: str) -> Any | None: ...
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
    async def get_room_user_message_by_message_id(self, message_id: str) -> Any | None: ...
    async def resolve_client_request_id_for_message_id(self, message_id: str) -> str | None: ...
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


class HITLContinuationPort(Protocol):
    async def reply_to_agent_task(
        self,
        *,
        request: Any,
        user_input: str,
    ) -> dict[str, Any]: ...

    async def resume_queue_from_continuation(
        self,
        continuation_message_id: str,
        *,
        task_result_text: str | None = None,
        failed: bool = False,
    ) -> bool: ...


class HITLTaskNotificationPort(Protocol):
    async def notify_task_update(
        self,
        message_id: str,
        state: str,
        *,
        room_id: str,
        user_id: str,
    ) -> bool: ...


class AgentTaskNotificationPort(Protocol):
    async def notify_task_update(
        self,
        message_id: str,
        state: str,
        *,
        room_id: str,
        user_id: str,
        error: str | None = None,
        parts: list[dict] | None = None,
    ) -> bool: ...


class AgentResponseHandlerPort(Protocol):
    async def handle(self, event: Any) -> None: ...


class HITLDeliveryPort(Protocol):
    async def emit(self, event: HITLRequestEvent | HITLResolvedEvent) -> None: ...


class AgentDispatchPort(Protocol):
    async def dispatch(self, command: Any) -> Any: ...
    async def cancel(self, agent_id: str, task_id: str) -> bool: ...


class A2AServicePort(Protocol):
    """Reserved for direct execution calls into the shell-owned A2A runtime.

    Queue construction still receives this capability to preserve factory wiring,
    but the current queue executor does not invoke it directly. Add only methods
    that execution calls itself; transport-level A2A contracts remain local to the
    transport that uses them.
    """


class AgentHealthPort(Protocol):
    async def check_agent_health(self, agent: Any, *, timeout: float) -> tuple[bool, Any]: ...


class AgentResolverPort(Protocol):
    async def resolve(
        self,
        user_input: str,
        allowed_agent_ids: list[str] | None = None,
    ) -> Any: ...


class DebateServicePort(Protocol):
    async def inject_short_debate_for_agent_message(
        self, agent_message: RoomAgentMessage
    ) -> RoomAgentMessage | None: ...


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


class AgentRateLimitResultPort(Protocol):
    allowed: bool
    reason: str | None
    user_requests_used: int
    user_requests_limit: int | None
    system_requests_used: int
    system_requests_limit: int | None
    retry_after_seconds: int | None


class RateLimitPort(Protocol):
    async def check_rate_limit(
        self,
        agent_id: str,
        user_id: str,
        rate_limit_per_user: int | None,
        rate_limit_system: int | None,
    ) -> AgentRateLimitResultPort: ...

    async def record_request(self, agent_id: str, user_id: str) -> None: ...


class RoomCoordinatorPort(Protocol):
    """Reserved for direct execution calls into the shell-owned room coordinator.

    Supervisor wiring still carries the coordinator dependency for compatibility,
    but direct orchestration behavior is expressed through narrower execution
    ports. Add methods here only when SupervisorExecutor calls the coordinator
    itself.
    """


class RoomMemoryPort(Protocol):
    async def add_agent_response_to_memory(
        self,
        room_id: str,
        agent_id: str,
        agent_name: str,
        response_text: str,
        was_successful: bool = True,
        message_id: str | None = None,
    ) -> RoomCenterMemoryResponse: ...


class RoomRuntimePort(Protocol):
    def create_agent_message(
        self,
        room_id: str,
        related_message_id: str,
        agent_id: str,
        content: str,
        user_id: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        turn_id: str | None = None,
        client_request_id: str | None = None,
    ) -> RoomAgentMessage: ...

    async def process_agent_message(
        self,
        request: RoomCenterAgentMessageRequest,
        room_memory: RoomMemory | None = None,
        quoted_text: str | None = None,
        orchestration_user_message_id: str | None = None,
    ) -> RoomCenterAgentMessageResponse: ...

    async def update_agent_message_by_message_id(
        self, request: RoomCenterAgentMessageRequest
    ) -> RoomCenterAgentMessageResponse: ...


class SSEDeliveryPort(Protocol):
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
    ) -> None: ...

    async def send_rate_limit_error(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        reason: str,
        retry_after_seconds: int | None = None,
        user_requests_used: int = 0,
        user_requests_limit: int | None = None,
        system_requests_used: int = 0,
        system_requests_limit: int | None = None,
    ) -> None: ...

    async def send_agent_response(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        content: str,
        related_message_id: str | None = None,
        parts: list[dict[str, Any]] | None = None,
        client_request_id: str | None = None,
    ) -> None: ...

    async def send_error(
        self,
        room_id: str,
        error: str,
        message_id: str | None = None,
    ) -> None: ...

    def clear_cancellation(self, message_id: str) -> None: ...
    def get_token(self, message_id: str) -> CancellationToken | None: ...
    def create_token(self, message_id: str) -> CancellationToken: ...


class RunReadPort(Protocol):
    async def get_run(self, run_id: str) -> RunInfo | None: ...
    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]: ...


class CancellationStatePort(Protocol):
    async def cancel_message_and_broadcast(self, message_id: str) -> None: ...
    def clear_cancellation(self, message_id: str) -> None: ...


class CancellationStorePort(Protocol):
    async def cancel_message(
        self,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool: ...


class HITLMessageCancellationPort(Protocol):
    async def cancel_requests_for_message(self, message_id: str) -> None: ...


class AgentTaskCleanupPort(Protocol):
    async def cleanup_cancelled_message_tasks(
        self,
        *,
        room_id: str,
        message_id: str,
    ) -> None: ...


class ClientRequestIdResolver(Protocol):
    async def resolve_client_request_id(
        self,
        message_id: str | None,
        provided_client_request_id: str | None,
    ) -> str | None: ...


class RunLifecyclePort(Protocol):
    async def record_processing_status(
        self,
        room_id: str,
        status: ProcessingStatusLike,
        message_id: str | None,
        *,
        client_request_id: str | None = None,
        details: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any] | None: ...

    async def heal_diverged_runs(self, limit: int = 500) -> int: ...

    async def append_run_timeout_failure(
        self,
        room_id: str,
        run_id: str,
        *,
        stale_minutes: int,
    ) -> dict[str, Any] | None: ...
