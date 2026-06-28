from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol

from common.dto import MessageCommitted
from common.protocols import MemoryProjector
from common.utils.logger import get_logger

logger = get_logger(__name__)


class MessageProjectionCallable(Protocol):
    def __call__(
        self,
        room_id: str,
        message_id: str,
        *,
        room_agent_set: dict[str, str] | None = None,
        agent_name: str | None = None,
        was_successful: bool | None = None,
    ) -> Awaitable[dict]: ...


class ContextMemoryEventHandler:
    def __init__(
        self,
        projector: MemoryProjector,
        project_for_event: MessageProjectionCallable,
    ) -> None:
        self._projector = projector
        self._project_for_event = project_for_event

    async def handle_message_committed(self, event: MessageCommitted) -> None:
        try:
            status = await self._project_for_event(
                event.room_id,
                event.message_id,
                room_agent_set=event.room_agent_set,
                agent_name=event.agent_name,
                was_successful=event.was_successful,
            )
            if status.get("projected"):
                await self._projector.run_compaction(event.room_id)
        except Exception:
            logger.exception(
                "Context & Memory projection failed",
                extra={"room_id": event.room_id, "message_id": event.message_id},
            )
            raise
