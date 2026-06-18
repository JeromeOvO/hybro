from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import Enum
from typing import Any, Protocol

from common.dto import HITLRequestEvent, HITLResolvedEvent, RunInfo

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
    """Execution-facing A2A capability port."""


class AgentHealthPort(Protocol):
    async def check_agent_health(self, agent: Any, *, timeout: float) -> tuple[bool, Any]: ...


class AgentResolverPort(Protocol):
    async def resolve(
        self,
        user_input: str,
        allowed_agent_ids: list[str] | None = None,
    ) -> Any: ...


class DebateServicePort(Protocol):
    async def inject_short_debate_for_agent_message(self, *args: Any, **kwargs: Any) -> Any: ...


class NotificationServicePort(Protocol):
    async def send_task_update(self, *args: Any, **kwargs: Any) -> Any: ...


class RateLimitPort(Protocol):
    async def check_rate_limit(self, *args: Any, **kwargs: Any) -> Any: ...
    async def record_request(self, *args: Any, **kwargs: Any) -> Any: ...


class RoomCoordinatorPort(Protocol):
    """Execution-facing room coordinator port."""


class RoomMemoryPort(Protocol):
    async def add_agent_response_to_memory(self, *args: Any, **kwargs: Any) -> Any: ...


class RoomRuntimePort(Protocol):
    def create_agent_message(self, *args: Any, **kwargs: Any) -> Any: ...
    async def process_agent_message(self, *args: Any, **kwargs: Any) -> Any: ...
    async def update_agent_message_by_message_id(
        self, message_id: str, room_agent_message: Any
    ) -> Any: ...


class SSEDeliveryPort(Protocol):
    async def send_task_submitted(self, *args: Any, **kwargs: Any) -> Any: ...
    async def send_task_update(self, *args: Any, **kwargs: Any) -> Any: ...
    async def send_rate_limit_error(self, *args: Any, **kwargs: Any) -> Any: ...
    async def send_agent_response(self, *args: Any, **kwargs: Any) -> Any: ...
    async def send_error(self, *args: Any, **kwargs: Any) -> Any: ...
    def clear_cancellation(self, message_id: str) -> None: ...
    def get_token(self, message_id: str) -> Any | None: ...
    def create_token(self, message_id: str) -> Any: ...


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
