from __future__ import annotations

from typing import Any

from execution.orchestration.room_message_center import RoomMessageCenter


class BoundRoomMessageCenterProxy:
    def __init__(self) -> None:
        self._runtime: RoomMessageCenter | None = None

    def bind(self, runtime: RoomMessageCenter) -> None:
        self._runtime = runtime

    def _require_runtime(self) -> RoomMessageCenter:
        if self._runtime is None:
            raise RuntimeError("RoomMessageCenter has not been bound at startup")
        return self._runtime

    def __getattr__(self, name: str) -> Any:
        return getattr(self._require_runtime(), name)


def create_room_message_center(**kwargs: Any) -> RoomMessageCenter:
    runtime = RoomMessageCenter()
    for name, value in kwargs.items():
        setattr(runtime, name, value)
    return runtime


__all__ = [
    "BoundRoomMessageCenterProxy",
    "create_room_message_center",
]
