from __future__ import annotations

from typing import Any

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
    def __init__(self, agent_reply_transport) -> None:
        self._agent_reply_transport = agent_reply_transport

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
        # The legacy queue executor is retired; there is no direct continuation
        # left to resume. Legacy-owned Runs have drained before this phase.
        return False

    async def has_pending_queue_continuation(
        self,
        continuation_message_id: str,
    ) -> bool:
        return False


class HITLTerminalLifecycleAdapter:
    """Converge public run state after HITL termination."""

    def __init__(
        self,
        run_lifecycle,
        *,
        canonical_terminalizer=None,
    ) -> None:
        self._run_lifecycle = run_lifecycle
        self._canonical_terminalizer = canonical_terminalizer

    async def terminalize_owning_run(
        self,
        request,
        *,
        terminal_status: str,
        reason: str,
    ) -> None:
        run_id = request.orchestration_run_id
        if run_id and self._canonical_terminalizer is not None:
            handled = await self._canonical_terminalizer(
                request,
                terminal_status=terminal_status,
                reason=reason,
            )
            if handled:
                return
        await self._run_lifecycle.project_run_state(
            room_id=request.room_id,
            run_id=run_id or request.user_message_id,
            trigger_message_id=request.user_message_id,
            target_state=(
                RunState.CANCELED if terminal_status == "canceled" else RunState.FAILED
            ),
            terminal_reason=reason,
            causation_id=f"hitl-terminal:{request.request_id}:{terminal_status}",
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
