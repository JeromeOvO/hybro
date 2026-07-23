from __future__ import annotations

from collections.abc import Awaitable, Callable

from common.utils.a2a_helpers import is_terminal_task_state_value
from common.utils.logger import get_logger

logger = get_logger(__name__)


class CancellationStateC3Adapter:
    def __init__(self, delivery) -> None:
        self._delivery = delivery

    async def cancel_message_and_broadcast(self, message_id: str) -> None:
        await self._delivery.cancel_message_and_broadcast(message_id)

    def clear_cancellation(self, message_id: str) -> None:
        self._delivery.clear_cancellation(message_id)


class MongoCancellationStoreAdapter:
    def __init__(self, mongodb) -> None:
        self._mongodb = mongodb

    async def cancel_message(
        self,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool:
        return await self._mongodb.cancel_message(message_id, requested_by_user_id)


class HITLMessageCancellationAdapter:
    def __init__(self, hitl_manager) -> None:
        self._hitl_manager = hitl_manager

    async def cancel_requests_for_message(self, message_id: str) -> None:
        await self._hitl_manager.cancel_requests_for_message(message_id)


class AgentTaskCleanupAdapter:
    def __init__(
        self,
        *,
        message_task_store,
        get_agent_card_from_url,
        cancel_remote_task,
        notify_task_update: Callable[..., Awaitable[bool]],
    ) -> None:
        self._message_task_store = message_task_store
        self._get_agent_card_from_url = get_agent_card_from_url
        self._cancel_remote_task = cancel_remote_task
        self._notify_task_update = notify_task_update

    async def cleanup_cancelled_message_tasks(
        self,
        *,
        room_id: str,
        message_id: str,
    ) -> None:
        agent_msgs = await self._message_task_store.get_room_agent_messages_by_related_message_id(
            message_id
        )
        for agent_msg in agent_msgs:
            if not getattr(agent_msg, "has_task_tracking", False):
                continue
            task = (
                agent_msg.message_content.message_task
                if getattr(agent_msg, "message_content", None)
                else None
            )
            task_state = (
                getattr(getattr(task, "status", None), "state", None)
                if task is not None
                else None
            )
            if is_terminal_task_state_value(task_state):
                continue
            await self._message_task_store.update_task_state_on_message(
                agent_msg.message_id,
                "canceled",
                message_text="Task was canceled",
            )
            await self._notify_task_update(
                message_id=agent_msg.message_id,
                state="canceled",
                room_id=agent_msg.room_id,
                user_id=agent_msg.user_id or "",
            )
            if (
                getattr(agent_msg, "agent_url", None)
                and task
                and getattr(task, "id", None)
            ):
                try:
                    agent_card = await self._get_agent_card_from_url(
                        agent_msg.agent_url
                    )
                    await self._cancel_remote_task(agent_card, task.id)
                except Exception:
                    logger.debug("remote cancellation failed", exc_info=True)


__all__ = [
    "AgentTaskCleanupAdapter",
    "CancellationStateC3Adapter",
    "HITLMessageCancellationAdapter",
    "MongoCancellationStoreAdapter",
]
