from __future__ import annotations

from typing import Protocol, runtime_checkable

from common.dto import CompactionResult


@runtime_checkable
class ContextMemoryCompactionPort(Protocol):
    async def should_compact(self, room_id: str) -> bool: ...
    async def compact_if_needed(self, room_id: str) -> CompactionResult | None: ...
    async def compact_room_memory(
        self,
        room_id: str,
        room_memory: object | None = None,
    ) -> CompactionResult: ...


__all__ = ["ContextMemoryCompactionPort"]
