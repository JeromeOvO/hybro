from __future__ import annotations

from typing import Any

from execution.orchestration.room_message_center import (
    BoundRoomMessageCenterProxy,
    RoomMessageCenter,
    room_message_center,
)


def create_room_message_center(**kwargs: Any) -> RoomMessageCenter:
    return RoomMessageCenter(**kwargs)


__all__ = [
    "BoundRoomMessageCenterProxy",
    "create_room_message_center",
    "room_message_center",
]
