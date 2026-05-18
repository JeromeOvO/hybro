from __future__ import annotations

from typing import Any


class DatabaseHITLPersistenceAdapter:
    def __init__(self, database_service) -> None:
        self._database_service = database_service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._database_service, name)


class LegacyHITLDeliveryAdapter:
    def __init__(self, sse_manager) -> None:
        self._sse_manager = sse_manager

    async def emit_hitl_event(
        self,
        *,
        room_id: str,
        message_type: str,
        payload: dict[str, Any],
    ) -> None:
        await self._sse_manager.broadcast_to_room(room_id, message_type, payload)


class A2AHITLContinuationAdapter:
    def __init__(self, a2a_service, room_message_center_provider) -> None:
        self._a2a_service = a2a_service
        self._room_message_center_provider = room_message_center_provider

    async def reply_to_agent_task(self, *, request: Any, user_input: str) -> dict[str, Any]:
        return await self._a2a_service.reply_to_agent_task(
            request=request,
            user_input=user_input,
        )

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
    "DatabaseHITLPersistenceAdapter",
    "HITLTaskNotificationAdapter",
    "LegacyHITLDeliveryAdapter",
]
