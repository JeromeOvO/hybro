from typing import Any, Protocol, runtime_checkable

from common.dto import AssembledContext, MemorySearchResult


@runtime_checkable
class ContextAssemblyPort(Protocol):
    def assemble_supervisor_context_from_memory(
        self,
        room_memory_doc: Any,
        current_task: str,
        *,
        agent_registry: list[dict[str, Any]] | None = None,
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
class RoomMemoryCleanupPort(Protocol):
    async def delete_room_memory(self, room_id: str) -> bool: ...


__all__ = [
    "ContextAssemblyPort",
    "RoomMemoryCleanupPort",
]
