from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class OfflineQueueEntry:
    event: Any
    enqueued_at: float


class OfflineQueue:
    def __init__(
        self, *, max_size: int, ttl_seconds: int, clock=time.monotonic
    ) -> None:
        self._items: deque[OfflineQueueEntry] = deque()
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._clock = clock

    def append(self, event: Any) -> Any | None:
        dropped = (
            self._items.popleft().event if len(self._items) >= self._max_size else None
        )
        self._items.append(OfflineQueueEntry(event=event, enqueued_at=self._clock()))
        return dropped

    def pop_fresh(self) -> list[Any]:
        now = self._clock()
        fresh: list[Any] = []
        while self._items:
            entry = self._items.popleft()
            if now - entry.enqueued_at < self._ttl:
                fresh.append(entry.event)
        return fresh

    def sweep_expired(self) -> list[Any]:
        now = self._clock()
        expired: list[Any] = []
        kept: deque[OfflineQueueEntry] = deque()
        while self._items:
            entry = self._items.popleft()
            if now - entry.enqueued_at >= self._ttl:
                expired.append(entry.event)
            else:
                kept.append(entry)
        self._items = kept
        return expired

    def __len__(self) -> int:
        return len(self._items)


__all__ = ["OfflineQueue", "OfflineQueueEntry"]
