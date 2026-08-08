from __future__ import annotations

from collections.abc import Awaitable, Callable

from common.utils.a2a_helpers import is_terminal_task_state_value
from common.utils.logger import get_logger

logger = get_logger(__name__)


class CancellationStateAdapter:
    def __init__(self, control) -> None:
        self._control = control

    async def cancel_message_and_broadcast(self, message_id: str) -> None:
        await self._control.signal(message_id)

    def release_active_token(self, message_id: str) -> bool:
        return self._control.release_active_token(message_id)

    def clear_cancellation(self, message_id: str) -> None:
        self._control.clear_cancellation(message_id)


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

    async def _collect_descendants(self, message_id: str) -> list:
        agent_msgs = []
        frontier = [message_id]
        seen_message_ids = {message_id}
        while frontier:
            next_frontier: list[str] = []
            for parent_message_id in frontier:
                reader = getattr(
                    self._message_task_store,
                    "get_room_agent_messages_by_related_message_id_strict",
                    self._message_task_store.get_room_agent_messages_by_related_message_id,
                )
                children = await reader(parent_message_id)
                for child in children:
                    child_id = getattr(child, "message_id", None)
                    if not isinstance(child_id, str) or child_id in seen_message_ids:
                        continue
                    seen_message_ids.add(child_id)
                    agent_msgs.append(child)
                    next_frontier.append(child_id)
            frontier = next_frontier
        return agent_msgs

    async def _notify_canceled(self, agent_msg) -> None:
        notified = await self._notify_task_update(
            message_id=agent_msg.message_id,
            state="canceled",
            room_id=agent_msg.room_id,
            user_id=agent_msg.user_id or "",
        )
        if notified is not False:
            return
        reset = await self._message_task_store.reset_last_notified_state(
            agent_msg.message_id
        )
        if reset is False:
            raise RuntimeError(f"notification reset failed for {agent_msg.message_id}")
        retried = await self._notify_task_update(
            message_id=agent_msg.message_id,
            state="canceled",
            room_id=agent_msg.room_id,
            user_id=agent_msg.user_id or "",
        )
        if retried is False:
            raise RuntimeError(
                f"cancellation notification failed for {agent_msg.message_id}"
            )

    async def _cleanup_one(self, agent_msg) -> None:
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
            if (
                str(getattr(task_state, "value", task_state)) == "canceled"
                and getattr(agent_msg, "last_notified_state", None) != "canceled"
            ):
                await self._notify_canceled(agent_msg)
            return
        if getattr(agent_msg, "agent_url", None) and task and getattr(task, "id", None):
            try:
                agent_card = await self._get_agent_card_from_url(agent_msg.agent_url)
                remote_canceled = await self._cancel_remote_task(agent_card, task.id)
                if remote_canceled is False:
                    logger.info(
                        "remote agent does not support cancellation",
                        extra={"message_id": agent_msg.message_id},
                    )
            except Exception:
                logger.info(
                    "remote cancellation unavailable; continuing local cleanup",
                    extra={"message_id": agent_msg.message_id},
                    exc_info=True,
                )
        persisted = await self._message_task_store.update_task_state_on_message(
            agent_msg.message_id,
            "canceled",
            message_text="Task was canceled",
        )
        persisted_ok = persisted[0] if isinstance(persisted, tuple) else persisted
        if persisted_ok is False:
            raise RuntimeError(
                f"local cancellation persistence failed for {agent_msg.message_id}"
            )
        await self._notify_canceled(agent_msg)

    async def cleanup_cancelled_message_tasks(
        self,
        *,
        room_id: str,
        message_id: str,
    ) -> None:
        del room_id  # Each persisted child carries its authoritative room ID.
        for agent_msg in await self._collect_descendants(message_id):
            if not getattr(agent_msg, "has_task_tracking", False):
                continue
            await self._cleanup_one(agent_msg)


__all__ = [
    "AgentTaskCleanupAdapter",
    "CancellationStateAdapter",
    "HITLMessageCancellationAdapter",
]
