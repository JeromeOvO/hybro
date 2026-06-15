from __future__ import annotations

from collections.abc import Awaitable, Callable

from common.dto import MessageCommitted
from common.protocols import MemoryProjector
from common.utils.logger import get_logger

logger = get_logger(__name__)


class ContextMemoryEventHandler:
    def __init__(
        self,
        projector: MemoryProjector,
        project_for_event: Callable[[str, str], Awaitable[dict]],
    ) -> None:
        self._projector = projector
        self._project_for_event = project_for_event

    async def handle_message_committed(self, event: MessageCommitted) -> None:
        try:
            status = await self._project_for_event(event.room_id, event.message_id)
            if status.get("projected") or status.get("reason") == "duplicate":
                await self._projector.run_compaction(event.room_id)
        except Exception:
            logger.exception(
                "Context & Memory projection failed",
                extra={"room_id": event.room_id, "message_id": event.message_id},
            )
            raise
