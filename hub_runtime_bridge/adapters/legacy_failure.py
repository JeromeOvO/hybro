from __future__ import annotations

from a2a_adapter.task_status import build_failed_task_status
from common.dto import OfflineHubFailureCommand


class LegacyOfflineFailureAdapter:
    def __init__(self, *, database_service, sse_manager) -> None:
        self._db = database_service
        self._sse = sse_manager

    async def mark_hub_message_failed(self, command: OfflineHubFailureCommand) -> None:
        if not command.agent_message_id:
            return
        msg = await self._db.get_room_agent_message_by_message_id(
            command.agent_message_id
        )
        if msg:
            if msg.message_content is None:
                from models.room import MessageContent

                msg.message_content = MessageContent()
            msg.message_content.message_text = command.error_text
            try:
                if msg.message_content.message_task:
                    msg.message_content.message_task.status = build_failed_task_status(
                        command.error_text
                    )
            except Exception:
                pass
            await self._db.update_room_agent_message_by_message_id(
                command.agent_message_id, msg
            )
        if command.room_id:
            await self._sse.send_error(
                command.room_id,
                command.error_text,
                message_id=command.agent_message_id,
            )


__all__ = ["LegacyOfflineFailureAdapter"]
