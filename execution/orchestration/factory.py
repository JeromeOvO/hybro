from __future__ import annotations

from typing import Any

import execution.orchestration.room_message_center as _defaults
from execution.orchestration.room_message_center import (
    BoundRoomMessageCenterProxy,
    RoomMessageCenter,
    room_message_center,
)


def create_room_message_center(**kwargs: Any) -> RoomMessageCenter:
    deps: dict[str, Any] = {
        "room_services": _defaults.room_services,
        "database_service": _defaults.db_service,
        "sse_manager": _defaults.sse_manager,
        "room_coordinator_service": _defaults.room_coordinator_service,
        "openai_service": _defaults.openai_service,
        "notification_service": _defaults.notification_service,
        "agent_resolver_service": _defaults.agent_resolver_service,
        "a2a_service": _defaults.a2a_service,
        "task_service": _defaults.task_service,
        "room_memory_service": _defaults.room_memory_service,
        "debate_service": _defaults.debate_service,
        "rate_limit_service": _defaults.rate_limit_service,
        "room_supervisor_service": _defaults.room_supervisor_service,
        "hitl_coordinator": None,
        "task_notifications": None,
    }
    deps.update(kwargs)
    return RoomMessageCenter(**deps)


__all__ = [
    "BoundRoomMessageCenterProxy",
    "create_room_message_center",
    "room_message_center",
]
