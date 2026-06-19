from typing import Any, Protocol, runtime_checkable

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


@runtime_checkable
class ContextMemoryRuntime(Protocol):
    def assemble_supervisor_context_from_memory(
        self,
        room_memory_doc: Any,
        current_task: str,
        *,
        agent_registry: list[dict] | None = None,
        max_turns: int = 5,
        memory_search_results: list | None = None,
    ) -> AssembledContext: ...

    def assemble_agent_execution_context_from_memory(
        self,
        room_memory_doc: Any,
        current_task: str,
        *,
        agent_id: str | None = None,
        agent_name: str | None = None,
        room_awareness: str | None = None,
        quoted_text: str | None = None,
        agent_task: str | None = None,
        include_system_instruction: bool = True,
    ) -> AssembledContext: ...

    async def legacy_search(
        self,
        query: str,
        room_id: str,
        user_id: str | None = None,
        limit: int | None = None,
    ) -> dict: ...

    def get_budget_summary(self) -> dict[str, Any]: ...


__all__ = [
    "ContextAssembler",
    "ContextMemoryRuntime",
    "MemoryManager",
    "MemoryProjector",
]
