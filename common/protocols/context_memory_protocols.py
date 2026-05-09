from typing import Protocol, runtime_checkable

from common.dto import (
    AssembledContext,
    CompactionResult,
    MemorySearchResult,
    RoomMemoryInfo,
    UserMemory,
)


@runtime_checkable
class ContextAssembler(Protocol):
    async def assemble_context(
        self,
        room_id: str,
        message_id: str,
        token_budget: int,
        agent_id: str | None = None,
    ) -> AssembledContext: ...


@runtime_checkable
class MemoryManager(Protocol):
    async def get_room_memory(self, room_id: str) -> RoomMemoryInfo | None: ...
    async def search_memory(
        self, room_id: str, query: str, limit: int = 10
    ) -> list[MemorySearchResult]: ...
    async def get_user_memories(self, user_id: str) -> list[UserMemory]: ...
    async def delete_room_memory(self, room_id: str) -> bool: ...


@runtime_checkable
class MemoryProjector(Protocol):
    async def project_message(self, room_id: str, message_id: str) -> None: ...
    async def run_compaction(self, room_id: str) -> CompactionResult: ...


__all__ = ["ContextAssembler", "MemoryManager", "MemoryProjector"]
