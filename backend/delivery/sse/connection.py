import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

SNAPSHOT_FRAME_TYPE = "snapshot"


class SSEConnection:
    def __init__(
        self,
        *,
        room_id: str,
        connection_id: str,
        heartbeat_interval: float,
        queue_maxsize: int = 100,
        now: Callable[[], datetime],
        room_seq_reader: Callable[[str], Awaitable[int | None]] | None = None,
        snapshot_provider: Callable[[str], Awaitable[dict[str, Any] | None]]
        | None = None,
    ) -> None:
        self.room_id = room_id
        self.connection_id = connection_id
        self.heartbeat_interval = heartbeat_interval
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_maxsize)
        self.connected_at = now()
        self.is_active = True
        self._now = now
        self._room_seq_reader = room_seq_reader
        self._snapshot_provider = snapshot_provider
        # Slow-consumer recovery: instead of disconnecting on QueueFull, the
        # connection marks itself for resync; the client's gap detection
        # re-requests a snapshot (Room Stream Snapshot plan §7).
        self.needs_resync = False
        self.frames_dropped = 0
        self._resync_backoff_until: float | None = None

    async def send_frame(
        self,
        frame: dict[str, Any],
        *,
        droppable: bool | None = None,
    ) -> bool:
        """Enqueue one frame.

        Returns ``False`` only when the connection is actually closed (the
        broadcast loop treats ``False`` as dead). A full queue is a resync
        mark, NOT a close: the frame is dropped, the connection stays alive,
        and gap detection recovers the client via a snapshot re-request.
        """

        if not self.is_active:
            return False
        is_droppable = (
            frame.get("type") == SNAPSHOT_FRAME_TYPE if droppable is None else droppable
        )
        try:
            self.queue.put_nowait(frame)
            return True
        except asyncio.QueueFull:
            self.needs_resync = True
            self.frames_dropped += 1
            if not is_droppable:
                # Live deltas are never policy-dropped: evict pending
                # snapshots first and retry the delta.
                await self._evict_pending_snapshots()
                try:
                    self.queue.put_nowait(frame)
                    return True
                except asyncio.QueueFull:
                    pass
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
            frame = await asyncio.wait_for(
                self.queue.get(),
                timeout=self.heartbeat_interval if timeout is None else timeout,
            )
        except TimeoutError:
            frame = {
                "type": "heartbeat",
                "timestamp": self._now().isoformat(),
                "room_id": self.room_id,
                "data": await self._heartbeat_data(),
            }
        await self._maybe_enqueue_resync_snapshot()
        return frame

    async def get_message(self, timeout: float | None = None) -> str:
        return json.dumps(await self.next_frame(timeout=timeout))

    async def _heartbeat_data(self) -> dict[str, Any]:
        """Heartbeats carry the latest room_seq so clients detect gaps even
        when no delta flows (plan §7)."""

        if self._room_seq_reader is None:
            return {}
        try:
            room_seq = await asyncio.wait_for(
                self._room_seq_reader(self.room_id), timeout=0.5
            )
        except Exception:  # reader best-effort
            return {}
        if room_seq is None:
            return {}
        return {"room_seq": room_seq}

    async def _maybe_enqueue_resync_snapshot(self) -> None:
        if not self.needs_resync or self._snapshot_provider is None:
            return
        now = self._now().timestamp()
        if self._resync_backoff_until is not None and now < self._resync_backoff_until:
            return
        self._resync_backoff_until = now + 2.0
        try:
            data = await asyncio.wait_for(
                self._snapshot_provider(self.room_id), timeout=2.0
            )
        except Exception:  # client self-heals via gap detection
            return
        if data is None:
            return
        frame = {
            "type": SNAPSHOT_FRAME_TYPE,
            "timestamp": self._now().isoformat(),
            "room_id": self.room_id,
            "data": data,
        }
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            return
        self.needs_resync = False
        self._resync_backoff_until = None

    async def _evict_pending_snapshots(self) -> None:
        items: list[dict[str, Any]] = []
        while True:
            try:
                item = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            items.append(item)
        for item in items:
            if item.get("type") == SNAPSHOT_FRAME_TYPE:
                self.frames_dropped += 1
                continue
            try:
                self.queue.put_nowait(item)
            except asyncio.QueueFull:
                self.frames_dropped += 1

    def close(self) -> None:
        self.is_active = False


__all__ = ["SNAPSHOT_FRAME_TYPE", "SSEConnection"]
