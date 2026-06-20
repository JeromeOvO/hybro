from __future__ import annotations

from typing import Any


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
    ) -> dict[str, Any]:
        return await self._agent_reply_transport.reply_to_task(
            message_id=message_id,
            task_id=task_id,
            context_id=context_id,
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
    "HITLPersistenceAdapter",
    "HITLDeliveryAdapter",
    "HITLTaskNotificationAdapter",
]
