from __future__ import annotations

import asyncio
from typing import Any

from common.dto import ExecutionAck, ExecutionRequest, HITLRequest, HITLResponse, RunInfo
from common.observability import traced_create_task
from common.protocols import EventPublisher
from common.utils.logger import get_logger
from execution.events import emit_processing_status
from execution.hitl.translators import (
    hitl_cancel_none_to_success,
    hitl_response_dict_to_common,
    model_hitl_request_to_common,
)
from execution.ports import (
    AgentResponseHandlerPort,
    AgentTaskCleanupPort,
    CancellationStatePort,
    CancellationStorePort,
    ClientRequestIdResolver,
    HITLMessageCancellationPort,
    LegacyProcessingStatusPublisher,
    RunEventEnabled,
    RunLifecyclePort,
    RunReadPort,
    TaskFactory,
)
from execution.translators import room_response_to_execution_ack
from models.request import OrchestrationRequest, RoomCenterUserMessageRequest

logger = get_logger(__name__)


class ExecutionFacade:
    def __init__(
        self,
        *,
        room_center,
        room_message_center,
        hitl_service,
        run_lifecycle: RunLifecyclePort,
        run_reader: RunReadPort,
        cancellation_state: CancellationStatePort,
        cancellation_store: CancellationStorePort,
        hitl_message_cancellation: HITLMessageCancellationPort,
        agent_task_cleanup: AgentTaskCleanupPort,
        agent_response_handler: AgentResponseHandlerPort,
        event_publisher: EventPublisher,
        legacy_processing_status_publisher: LegacyProcessingStatusPublisher,
        run_event_enabled: RunEventEnabled,
        client_request_id_resolver: ClientRequestIdResolver,
        task_factory: TaskFactory = traced_create_task,
    ) -> None:
        self._room_center = room_center
        self._room_message_center = room_message_center
        self._hitl_service = hitl_service
        self._run_lifecycle = run_lifecycle
        self._run_reader = run_reader
        self._cancellation_state = cancellation_state
        self._cancellation_store = cancellation_store
        self._hitl_message_cancellation = hitl_message_cancellation
        self._agent_task_cleanup = agent_task_cleanup
        self._agent_response_handler = agent_response_handler
        self._event_publisher = event_publisher
        self._legacy_processing_status_publisher = legacy_processing_status_publisher
        self._run_event_enabled = run_event_enabled
        self._client_request_id_resolver = client_request_id_resolver
        self._task_factory = task_factory
        self._inflight: set[asyncio.Task] = set()

    async def execute(self, request: ExecutionRequest) -> ExecutionAck:
        room_request = RoomCenterUserMessageRequest.model_construct(
            room_id=request.room_id,
            user_id=request.sender_id,
            user_name=request.sender_name,
            message=request.message,
            attachments=request.attachments,
            inline_file_ids=request.inline_file_ids,
            client_request_id=request.client_request_id,
        )
        response = await self._room_center.send_message_to_room(
            room_request,
            request.target_group,
            request.mentioned_agent_ids,
        )
        return room_response_to_execution_ack(response)

    async def start_orchestration(
        self,
        request: ExecutionRequest,
        ack: ExecutionAck,
    ) -> None:
        if not ack.success or not ack.message_id:
            return
        orchestration_request = OrchestrationRequest(
            room_id=request.room_id,
            room_user_message_id=ack.message_id,
            room_related_message_id=request.parent_message_id,
            user_id=request.sender_id,
            client_request_id=request.client_request_id,
        )
        task = self._spawn_orchestration(
            self._room_message_center.process_room_user_message(orchestration_request),
            name=f"execution-orchestrate-{ack.message_id}",
        )
        await task

    def schedule_recovery_orchestration(
        self,
        request: OrchestrationRequest,
        *,
        reason: str,
    ) -> asyncio.Task[Any]:
        message_id = request.room_user_message_id or request.room_agent_message_id or "unknown"
        return self._spawn_orchestration(
            self._room_message_center.process_room_user_message(request),
            name=f"execution-recovery-{reason}-{message_id}",
        )

    def _spawn_orchestration(
        self,
        coro,
        *,
        name: str,
    ) -> asyncio.Task[Any]:
        task = self._task_factory(coro, name=name)
        self._inflight.add(task)

        def _on_done(done: asyncio.Task) -> None:
            self._inflight.discard(done)
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                logger.error(
                    "execution orchestration task failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_on_done)
        return task

    async def cancel(
        self,
        room_id: str,
        message_id: str,
        *,
        requested_by_user_id: str,
    ) -> bool:
        await self._cancellation_state.cancel_message_and_broadcast(message_id)
        await self._hitl_message_cancellation.cancel_requests_for_message(message_id)
        persisted = await self._cancellation_store.cancel_message(
            message_id,
            requested_by_user_id,
        )
        if not persisted:
            self._cancellation_state.clear_cancellation(message_id)
            return False
        await emit_processing_status(
            room_id=room_id,
            status="canceled",
            message_id=message_id,
            lifecycle_message_id=message_id,
            run_lifecycle=self._run_lifecycle,
            event_publisher=self._event_publisher,
            legacy_processing_status_publisher=self._legacy_processing_status_publisher,
            run_event_enabled=self._run_event_enabled,
            client_request_id_resolver=self._client_request_id_resolver,
        )
        try:
            await self._agent_task_cleanup.cleanup_cancelled_message_tasks(
                room_id=room_id,
                message_id=message_id,
            )
        except Exception:
            logger.warning("agent task cleanup failed for cancellation", exc_info=True)
        return True

    async def get_run(self, run_id: str) -> RunInfo | None:
        return await self._run_reader.get_run(run_id)

    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]:
        return await self._run_reader.get_runs_for_room(room_id)

    async def cancel_inflight_tasks(self) -> int:
        tasks = set(self._inflight)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    async def heal_diverged_runs(self, limit: int = 500) -> int:
        return await self._run_lifecycle.heal_diverged_runs(limit=limit)

    async def create_hitl_request(
        self,
        room_id: str,
        user_message_id: str,
        prompt: str,
        source: str,
        **kwargs: Any,
    ) -> HITLRequest | None:
        result = await self._hitl_service.request_input(
            room_id=room_id,
            user_message_id=user_message_id,
            source=source,
            prompt=prompt,
            **kwargs,
        )
        return model_hitl_request_to_common(result) if result is not None else None

    async def resolve_hitl(
        self,
        room_id: str,
        request_id: str,
        response: str,
        responder_id: str,
    ) -> HITLResponse:
        result = await self._hitl_service.handle_response(
            room_id=room_id,
            request_id=request_id,
            user_input=response,
            user_id=responder_id,
        )
        return hitl_response_dict_to_common(result)

    async def get_pending_hitl(self, room_id: str) -> list[HITLRequest]:
        requests = await self._hitl_service.get_pending_requests(room_id)
        return [model_hitl_request_to_common(request) for request in requests]

    async def cancel_hitl(self, room_id: str, request_id: str) -> bool:
        result = await self._hitl_service.cancel_request(request_id, room_id=room_id)
        return hitl_cancel_none_to_success(result)


__all__ = ["ExecutionFacade"]
