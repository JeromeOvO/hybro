from __future__ import annotations

from execution.orchestration.room_message_center import room_message_center


def get_bound_room_message_center():
    return room_message_center


__all__ = ["get_bound_room_message_center"]
