from __future__ import annotations

from typing import Literal

from common.dto import MessageCommitted
from common.eventing import InternalEventPublisher
from common.utils.time import utcnow

MessageCommitType = Literal["user", "agent"]


async def publish_message_committed(
    internal_event_publisher: InternalEventPublisher,
    *,
    room_id: str,
    message_id: str,
    message_type: MessageCommitType,
    agent_id: str | None = None,
    room_agent_set: dict[str, str] | None = None,
    agent_name: str | None = None,
    was_successful: bool | None = None,
    wait_for_handlers: bool = False,
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
    await internal_event_publisher.publish(
        event,
        wait_for_handlers=wait_for_handlers,
        fanout=False,
    )


__all__ = ["MessageCommitType", "publish_message_committed"]
