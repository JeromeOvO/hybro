from __future__ import annotations

from typing import Any, Protocol

from common.utils.logger import get_logger

logger = get_logger(__name__)


class TaskUpdateDeliveryPort(Protocol):
    async def send_task_update(
        self,
        room_id: str,
        message_id: str,
        status: Any,
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


class TaskUpdateNotifier:
    def __init__(self, delivery: TaskUpdateDeliveryPort) -> None:
        self.delivery = delivery

    async def send_task_update(
        self,
        *,
        room_id: str,
        message_id: str | None,
        status: Any,
        agent_card: Any = None,
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
        parts: list[dict] | None = None,
        client_request_id: str | None = None,
    ) -> None:
        if not message_id:
            logger.warning("TaskUpdateNotifier: skipping update with no message_id")
            return

        final_agent_name = agent_name
        if not final_agent_name and agent_card:
            final_agent_name = agent_card.name

        await self.delivery.send_task_update(
            room_id=room_id,
            message_id=message_id,
            status=status,
            content=content,
            error=error,
            agent_name=final_agent_name,
            agent_id=agent_id,
            created_at=created_at,
            requires_input=bool(requires_input),
            requires_auth=bool(requires_auth),
            status_message=status_message,
            step_number=step_number,
            total_steps=total_steps,
            task_content=task_content,
            related_message_id=related_message_id,
            parts=parts,
            client_request_id=client_request_id,
        )


__all__ = ["TaskUpdateDeliveryPort", "TaskUpdateNotifier"]
