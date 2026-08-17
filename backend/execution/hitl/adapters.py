from __future__ import annotations

from typing import Any

from execution.orchestration.run_reducer import record_hitl_terminalization
from execution.orchestration.run_store import (
    DuplicateEventIdConflict,
    OrchestrationStoreConflict,
)
from models.orchestration import (
    TERMINAL_ORCHESTRATION_STATUSES,
    OrchestrationEventType,
    OrchestrationRunEvent,
    OrchestrationStatus,
)
from models.run import RunState


class HITLPersistenceAdapter:
    def __init__(self, persistence) -> None:
        self._persistence = persistence

    def __getattr__(self, name: str) -> Any:
        return getattr(self._persistence, name)


class HITLDeliveryAdapter:
    def __init__(self, event_publisher) -> None:
        self._event_publisher = event_publisher

    async def emit(self, event) -> None:
        await self._event_publisher.emit(event)


class A2AHITLContinuationAdapter:
    def __init__(self, agent_reply_transport, room_message_center_provider) -> None:
        self._agent_reply_transport = agent_reply_transport
        self._room_message_center_provider = room_message_center_provider

    async def reply_to_task(
        self,
        *,
        message_id: str,
        task_id: str,
        context_id: str,
        user_input: str,
        outbound_message_id: str | None = None,
    ) -> dict[str, Any]:
        kwargs = {
            "message_id": message_id,
            "task_id": task_id,
            "context_id": context_id,
            "user_input": user_input,
        }
        if outbound_message_id is not None:
            kwargs["outbound_message_id"] = outbound_message_id
        return await self._agent_reply_transport.reply_to_task(**kwargs)

    async def resume_queue_from_continuation(
        self,
        continuation_message_id: str,
        *,
        task_result_text: str | None = None,
        failed: bool = False,
    ) -> bool:
        room_message_center = self._room_message_center_provider()
        return await room_message_center.resume_queue_from_continuation(
            continuation_message_id,
            task_result_text=task_result_text,
            failed=failed,
        )


class HITLTerminalLifecycleAdapter:
    """Converge orchestration and public run state after HITL termination."""

    def __init__(self, orchestration_run_store, run_lifecycle) -> None:
        self._orchestration_run_store = orchestration_run_store
        self._run_lifecycle = run_lifecycle

    async def terminalize_owning_run(
        self,
        request,
        *,
        terminal_status: str,
        reason: str,
    ) -> None:
        orchestration_state = None
        run_id = request.orchestration_run_id
        terminal_event_required = False
        if run_id:
            for _attempt in range(3):
                current = await self._orchestration_run_store.get_run(run_id)
                if current is None:
                    break
                if current.status in TERMINAL_ORCHESTRATION_STATUSES:
                    orchestration_state = current
                    terminal_event_required = any(
                        entry.get("request_id") == request.request_id
                        and entry.get("code") == f"hitl_{terminal_status}"
                        for entry in current.decision_log
                        if isinstance(entry, dict)
                    )
                    break
                updated = record_hitl_terminalization(
                    current,
                    request_id=request.request_id,
                    terminal_status=terminal_status,
                    reason=reason,
                )
                try:
                    orchestration_state = (
                        await self._orchestration_run_store.save_state(
                            updated,
                            expected_version=current.state_version,
                        )
                    )
                except OrchestrationStoreConflict:
                    continue
                terminal_event_required = True
                break
            else:
                raise OrchestrationStoreConflict(
                    f"failed to terminalize orchestration run {run_id!r}"
                )

        if orchestration_state is not None and terminal_event_required:
            event_id = (
                f"{orchestration_state.run_id}:hitl-terminal:"
                f"{request.request_id}:{terminal_status}"
            )
            try:
                await self._orchestration_run_store.append_event(
                    OrchestrationRunEvent(
                        event_id=event_id,
                        run_id=orchestration_state.run_id,
                        room_id=orchestration_state.room_id,
                        type=OrchestrationEventType.RUN_TERMINAL,
                        state_version=orchestration_state.state_version,
                        payload={
                            "reason": reason,
                            "hitl_request_id": request.request_id,
                            "hitl_status": terminal_status,
                        },
                    )
                )
            except DuplicateEventIdConflict:
                pass

        target_state = (
            RunState.CANCELED if terminal_status == "canceled" else RunState.FAILED
        )
        if orchestration_state is not None:
            target_state = {
                OrchestrationStatus.CANCELED: RunState.CANCELED,
                OrchestrationStatus.COMPLETED: RunState.COMPLETED,
            }.get(orchestration_state.status, RunState.FAILED)
        await self._run_lifecycle.project_run_state(
            room_id=request.room_id,
            run_id=run_id or request.user_message_id,
            trigger_message_id=request.user_message_id,
            target_state=target_state,
            terminal_reason=reason,
            causation_id=(f"hitl-terminal:{request.request_id}:{terminal_status}"),
            client_request_id=request.client_request_id,
        )


class HITLTaskNotificationAdapter:
    def __init__(self, notify_task_update) -> None:
        self._notify_task_update = notify_task_update

    async def notify_task_update(
        self,
        message_id: str,
        state: str,
        *,
        room_id: str,
        user_id: str,
    ) -> bool:
        return await self._notify_task_update(
            message_id=message_id,
            state=state,
            room_id=room_id,
            user_id=user_id,
        )


__all__ = [
    "A2AHITLContinuationAdapter",
    "HITLPersistenceAdapter",
    "HITLTerminalLifecycleAdapter",
    "HITLDeliveryAdapter",
    "HITLTaskNotificationAdapter",
]
