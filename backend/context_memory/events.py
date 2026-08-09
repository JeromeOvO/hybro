from __future__ import annotations

from common.dto import MessageCommitted
from common.protocols import ProjectionPort
from common.utils.logger import get_logger

logger = get_logger(__name__)


class ContextMemoryEventHandler:
    def __init__(self, projection: ProjectionPort) -> None:
        self._projection = projection

    async def handle_message_committed(self, event: MessageCommitted) -> None:
        try:
            status = await self._projection.project_message_for_event(
                event.room_id,
                event.message_id,
                room_agent_set=event.room_agent_set,
                agent_name=event.agent_name,
                was_successful=event.was_successful,
            )
            if status.get("projected"):
                await self._projection.run_compaction(event.room_id)
        except Exception:
            logger.exception(
                "Context & Memory projection failed",
                extra={"room_id": event.room_id, "message_id": event.message_id},
            )
            raise
