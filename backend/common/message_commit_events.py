from __future__ import annotations

from typing import Literal

from common.dto import MessageCommitted
from common.protocols import EventPublisher
from common.utils.time import utcnow

MessageCommitType = Literal["user", "agent"]


async def publish_message_committed(
    event_publisher: EventPublisher,
    *,
    room_id: str,
    message_id: str,
    message_type: MessageCommitType,
    agent_id: str | None = None,
    room_agent_set: dict[str, str] | None = None,
    agent_name: str | None = None,
    was_successful: bool | None = None,
    wait_for_local_handlers: bool = False,
) -> None:
    event = MessageCommitted(
        timestamp=utcnow(),
        payload={},
        room_id=room_id,
        message_id=message_id,
        message_type=message_type,
        agent_id=agent_id,
        room_agent_set=room_agent_set,
        agent_name=agent_name,
        was_successful=was_successful,
    )
    if wait_for_local_handlers:
        await event_publisher.emit_internal(
            event,
            wait_for_local_handlers=True,
            broadcast=False,
        )
        return
    await event_publisher.emit_internal(event, broadcast=False)


__all__ = ["MessageCommitType", "publish_message_committed"]
