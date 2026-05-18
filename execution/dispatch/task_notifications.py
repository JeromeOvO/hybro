from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class TaskNotificationAdapter:
    def __init__(
        self,
        notify_task_update: Callable[..., Awaitable[bool]],
        *,
        state_converter: Callable[[str], Any] | None = None,
    ) -> None:
        self._notify_task_update = notify_task_update
        self._state_converter = state_converter or (lambda value: value)

    async def notify_task_update(
        self,
        *,
        message_id: str,
        state: str,
        room_id: str,
        user_id: str,
        error: str | None = None,
        parts: list[dict] | None = None,
    ) -> bool:
        return await self._notify_task_update(
            message_id=message_id,
            state=self._state_converter(state),
            room_id=room_id,
            user_id=user_id,
            error=error,
            parts=parts,
        )


__all__ = ["TaskNotificationAdapter"]
