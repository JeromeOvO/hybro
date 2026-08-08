import asyncio
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any


class SSEConnection:
    def __init__(
        self,
        *,
        room_id: str,
        connection_id: str,
        heartbeat_interval: float,
        queue_maxsize: int = 100,
        now: Callable[[], datetime],
    ) -> None:
        self.room_id = room_id
        self.connection_id = connection_id
        self.heartbeat_interval = heartbeat_interval
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_maxsize)
        self.connected_at = now()
        self.is_active = True
        self._now = now

    async def send_frame(self, frame: dict[str, Any]) -> bool:
        if not self.is_active:
            return False
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.close()
            return False
        return True

    async def send_message(self, message_type: str, data: Any) -> bool:
        frame = {
            "type": message_type,
            "timestamp": self._now().isoformat(),
            "room_id": self.room_id,
            "data": data,
        }
        return await self.send_frame(frame)

    async def next_frame(self, timeout: float | None = None) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                self.queue.get(),
                timeout=self.heartbeat_interval if timeout is None else timeout,
            )
        except TimeoutError:
            return {
                "type": "heartbeat",
                "timestamp": self._now().isoformat(),
                "room_id": self.room_id,
                "data": {},
            }

    async def get_message(self, timeout: float | None = None) -> str:
        return json.dumps(await self.next_frame(timeout=timeout))

    def close(self) -> None:
        self.is_active = False


__all__ = ["SSEConnection"]
