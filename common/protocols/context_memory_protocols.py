from typing import Protocol, runtime_checkable

from common.dto import (
    AssembledContext,
    CompactionResult,
    ContextBlock,
    MemorySearchResult,
    RoomMemoryInfo,
    UserMemory,
)


@runtime_checkable
class ContextAssembler(Protocol):
    async def build_supervisor_context(self, room_id: str) -> AssembledContext: ...
    async def build_agent_execution_context(
        self, room_id: str, agent_id: str
    ) -> AssembledContext: ...


@runtime_checkable
class MemoryManager(Protocol):
    async def list_room_memory(self, room_id: str) -> list[RoomMemoryInfo]: ...
    async def list_user_memory(self, user_id: str) -> list[UserMemory]: ...
    async def compact_if_needed(self, room_id: str) -> CompactionResult | None: ...
    async def compact_room_memory(self, room_id: str) -> CompactionResult: ...


@runtime_checkable
class MemoryProjector(Protocol):
    async def should_compact(self, room_id: str, blocks: list[ContextBlock]) -> bool: ...
    async def search_memory(
        self, room_id: str, query: str, limit: int | None = None
    ) -> list[MemorySearchResult]: ...


__all__ = [
    "ContextAssembler",
    "MemoryManager",
    "MemoryProjector",
]
