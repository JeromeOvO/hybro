from __future__ import annotations

from typing import Any

from execution.orchestration.room_message_center import (
    BoundRoomMessageCenterProxy,
    RoomMessageCenter,
    room_message_center,
)


def create_room_message_center(**kwargs: Any) -> RoomMessageCenter:
    runtime = RoomMessageCenter()
    for name, value in kwargs.items():
        setattr(runtime, name, value)
    return runtime


__all__ = [
    "BoundRoomMessageCenterProxy",
    "create_room_message_center",
    "room_message_center",
]
