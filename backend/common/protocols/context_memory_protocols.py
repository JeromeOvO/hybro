from typing import Any, Protocol, runtime_checkable

from common.dto import AssembledContext, CompactionResult, MemorySearchResult


@runtime_checkable
class ContextAssemblyPort(Protocol):
    def assemble_supervisor_context_from_memory(
        self,
        room_memory_doc: Any,
        current_task: str,
        *,
        agent_registry: list[dict] | None = None,
        max_turns: int = 5,
        memory_search_results: list[MemorySearchResult] | None = None,
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


@runtime_checkable
class MemorySearchPort(Protocol):
    async def search_memory(
        self,
        room_id: str,
        query: str,
        limit: int = 10,
    ) -> list[MemorySearchResult]: ...


@runtime_checkable
class ProjectionPort(Protocol):
    async def project_message_for_event(
        self,
        room_id: str,
        message_id: str,
        *,
        room_agent_set: dict[str, str] | None = None,
        agent_name: str | None = None,
        was_successful: bool | None = None,
    ) -> dict[str, Any]: ...

    async def run_compaction(self, room_id: str) -> CompactionResult: ...


@runtime_checkable
class CompactionPort(Protocol):
    async def should_compact(self, room_id: str) -> bool: ...
    async def compact_if_needed(self, room_id: str) -> CompactionResult | None: ...
    async def compact_room_memory(
        self,
        room_id: str,
        room_memory_doc: Any | None = None,
    ) -> CompactionResult: ...


@runtime_checkable
class RoomMemoryCleanupPort(Protocol):
    async def delete_room_memory(self, room_id: str) -> bool: ...


__all__ = [
    "CompactionPort",
    "ContextAssemblyPort",
    "MemorySearchPort",
    "ProjectionPort",
    "RoomMemoryCleanupPort",
]
