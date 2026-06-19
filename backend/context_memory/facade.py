from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from common.dto import (
    AssembledContext,
    CompactionResult,
    MemorySearchResult,
    RoomMemoryInfo,
    UserMemory,
)
from common.observability import NoopTracingProvider
from common.protocols import (
    ContentStorageRepository,
    LLMGateway,
    MemoryRepository,
    RoomHistoryReader,
    VectorDAL,
)
from common.utils.logger import get_logger
from context_memory import assembly, compaction, projection, search, summary
from context_memory.config import (
    CompactionConfig,
    ContextMemoryLLMConfig,
    MemorySearchConfig,
    TokenBudgetConfig,
)
from context_memory.content_storage import (
    content_from_document,
    expand_mongodb_reference,
    store_full_content,
)
from context_memory.translators import (
    normalize_room_memory,
    room_memory_info_from_doc,
    user_memory_from_doc,
)

logger = get_logger(__name__)


class ContextMemoryFacade:
    def __init__(
        self,
        *,
        memory_repository: MemoryRepository,
        content_repository: ContentStorageRepository,
        room_history_reader: RoomHistoryReader,
        vector: VectorDAL,
        llm_provider: LLMGateway,
        id_factory: Callable[[], str],
        now: Callable[[], datetime],
        token_budget: TokenBudgetConfig | None = None,
        compaction_config: CompactionConfig | None = None,
        search_config: MemorySearchConfig | None = None,
        llm_config: ContextMemoryLLMConfig | None = None,
        background_task_runner: Callable[[Awaitable[Any]], None] | None = None,
        tracer: Any | None = None,
    ) -> None:
        self.memory_repository = memory_repository
        self.content_repository = content_repository
        self.room_history_reader = room_history_reader
        self.vector = vector
        self.llm_provider = llm_provider
        self.id_factory = id_factory
        self.now = now
        self.token_budget = token_budget or TokenBudgetConfig()
        self.compaction_config = compaction_config or CompactionConfig()
        self.search_config = search_config or MemorySearchConfig()
        self.llm_config = llm_config or ContextMemoryLLMConfig()
        self.background_task_runner = background_task_runner or _background_task
        self.tracer = tracer or NoopTracingProvider()

    async def assemble_context(
        self,
        room_id: str,
        message_id: str,
        token_budget: int,
        agent_id: str | None = None,
    ) -> AssembledContext:
        doc = await self.memory_repository.get_room_memory(room_id)
        if not doc:
            doc = projection.new_room_memory_doc(
                room_id=room_id,
                memory_id=self.id_factory(),
                now=self.now(),
            )
        state = normalize_room_memory(doc)
        current_task = None
        projected_turn_id = f"message:{message_id}"
        for turn in state.conversation_history:
            if turn.turn_id == projected_turn_id:
                current_task = turn.content
                break
        if current_task is None:
            messages = await self.room_history_reader.get_messages_by_ids([message_id])
            message = next(
                (
                    item
                    for item in messages
                    if getattr(item, "message_id", None) == message_id
                    and getattr(item, "room_id", None) == room_id
                ),
                None,
            )
            if message is None:
                raise ValueError(f"Message {message_id} not found in room {room_id}")
            current_task = projection.extract_message_text(getattr(message, "content", None))

        budget = self.token_budget.with_model_window(token_budget)
        if agent_id is None:
            result = assembly.assemble_supervisor_context_from_memory(
                doc,
                current_task or "",
                token_budget=budget,
            )
        else:
            result = assembly.assemble_agent_execution_context_from_memory(
                doc,
                current_task or "",
                token_budget=budget,
                agent_id=agent_id,
            )
        metadata = dict(result.metadata)
        metadata.update({"message_id": message_id, "agent_id": agent_id})
        return result.model_copy(update={"metadata": metadata})

    async def get_room_memory(self, room_id: str) -> RoomMemoryInfo | None:
        doc = await self.memory_repository.get_room_memory(room_id)
        return room_memory_info_from_doc(doc) if doc else None

    async def search_memory(
        self, room_id: str, query: str, limit: int = 10
    ) -> list[MemorySearchResult]:
        results, _response = await search.search_memory(
            room_id=room_id,
            query=query,
            limit=limit,
            vector=self.vector,
            llm_provider=self.llm_provider,
            content_repository=self.content_repository,
            config=self.search_config,
        )
        return results

    async def get_user_memories(self, user_id: str) -> list[UserMemory]:
        docs = await self.memory_repository.get_user_memories(user_id)
        return [user_memory_from_doc(doc) for doc in docs]

    async def delete_room_memory(self, room_id: str) -> bool:
        existed = await self.memory_repository.get_room_memory(room_id)
        if existed:
            memory_deleted = await self.memory_repository.delete_room_memory(room_id)
            if not memory_deleted:
                return False
        cleanup_ok = True
        try:
            vector_deleted = await search.delete_room_index(
                room_id=room_id,
                vector=self.vector,
                config=self.search_config,
                unavailable_ok=True,
            )
            cleanup_ok = bool(vector_deleted) and cleanup_ok
        except Exception:
            logger.exception(
                "Failed to clean up context memory vector index",
                extra={"room_id": room_id},
            )
            cleanup_ok = False
        try:
            await self.content_repository.delete_content_by_room_id(room_id)
        except Exception:
            logger.exception(
                "Failed to clean up context memory content",
                extra={"room_id": room_id},
            )
            cleanup_ok = False
        return cleanup_ok

    async def project_message(self, room_id: str, message_id: str) -> None:
        await self.project_message_for_event(room_id, message_id)

    async def project_message_for_event(self, room_id: str, message_id: str) -> dict:
        return await projection.project_message_from_history(
            room_id=room_id,
            message_id=message_id,
            repository=self.memory_repository,
            room_history_reader=self.room_history_reader,
            id_factory=self.id_factory,
            now=self.now,
        )

    async def run_compaction(self, room_id: str) -> CompactionResult:
        return await compaction.run_compaction(
            repository=self.memory_repository,
            content_repository=self.content_repository,
            room_id=room_id,
            config=self.compaction_config,
            now=self.now,
            index_turn=self.index_turn_for_search,
        )

    def assemble_supervisor_context_from_memory(
        self,
        room_memory_doc: dict,
        current_task: str,
        *,
        agent_registry: list[dict] | None = None,
        max_turns: int = 5,
        memory_search_results: list | None = None,
    ) -> AssembledContext:
        return assembly.assemble_supervisor_context_from_memory(
            room_memory_doc,
            current_task,
            token_budget=self.token_budget,
            agent_registry=agent_registry,
            max_turns=max_turns,
            memory_search_results=memory_search_results,
        )

    def assemble_agent_execution_context_from_memory(
        self,
        room_memory_doc: dict,
        current_task: str,
        *,
        agent_id: str | None = None,
        agent_name: str | None = None,
        room_awareness: str | None = None,
        quoted_text: str | None = None,
        agent_task: str | None = None,
        include_system_instruction: bool = True,
    ) -> AssembledContext:
        return assembly.assemble_agent_execution_context_from_memory(
            room_memory_doc,
            current_task,
            token_budget=self.token_budget,
            agent_id=agent_id,
            agent_name=agent_name,
            room_awareness=room_awareness,
            quoted_text=quoted_text,
            agent_task=agent_task,
            include_system_instruction=include_system_instruction,
        )

    def get_budget_summary(self) -> dict[str, int]:
        return self.token_budget.get_budget_summary()

    async def legacy_create_room_memory(self, memory_doc: dict) -> dict | None:
        doc = dict(memory_doc)
        doc.setdefault("memory_id", self.id_factory())
        await self.memory_repository.create_room_memory(doc)
        return doc

    async def legacy_get_room_memory_by_room_id(self, room_id: str) -> dict | None:
        return await self.memory_repository.get_room_memory(room_id)

    async def legacy_get_room_memory_by_memory_id(
        self, memory_id: str
    ) -> dict | None:
        return await self.memory_repository.get_room_memory_by_memory_id(memory_id)

    async def legacy_update_room_memory_by_room_id(
        self, room_id: str, memory_doc: dict
    ) -> bool:
        return await self.memory_repository.update_room_memory_by_room_id(
            room_id, memory_doc
        )

    async def legacy_get_room_memory_for_update_by_memory_id(
        self, memory_id: str
    ) -> dict | None:
        return await self.memory_repository.get_room_memory_by_memory_id(memory_id)

    async def legacy_update_room_memory_by_memory_id(
        self, memory_id: str, memory_doc: dict
    ) -> bool:
        return await self.memory_repository.update_room_memory_by_memory_id(
            memory_id, memory_doc
        )

    async def legacy_delete_room_memory_by_room_id(self, room_id: str) -> bool:
        return await self.delete_room_memory(room_id)

    async def legacy_delete_room_memory_by_memory_id(self, memory_id: str) -> bool:
        doc = await self.memory_repository.get_room_memory_by_memory_id(memory_id)
        if not doc:
            return False
        room_id = doc.get("room_id")
        if not room_id:
            return False
        return await self.delete_room_memory(room_id)

    async def initialize_or_update_room_memory(
        self,
        room_id: str,
        *,
        memory_content: str | None,
        room_agent_set: dict | None,
        user_id: str | None,
        attachments: list | None = None,
        message_id: str | None = None,
    ) -> dict | None:
        return await projection.initialize_or_update_room_memory(
            repository=self.memory_repository,
            room_id=room_id,
            memory_content=memory_content,
            room_agent_set=room_agent_set,
            user_id=user_id,
            attachments=attachments,
            id_factory=self.id_factory,
            now=self.now,
            message_id=message_id,
        )

    async def add_agent_response_to_memory(
        self,
        room_id: str,
        agent_id: str,
        agent_name: str,
        response_text: str,
        was_successful: bool = True,
        message_id: str | None = None,
    ) -> tuple[bool, bool]:
        return await projection.add_agent_response_to_memory(
            repository=self.memory_repository,
            room_id=room_id,
            agent_id=agent_id,
            agent_name=agent_name,
            response_text=response_text,
            was_successful=was_successful,
            id_factory=self.id_factory,
            now=self.now,
            llm_provider=self.llm_provider,
            llm_config=self.llm_config,
            background_task_runner=self.background_task_runner,
            message_id=message_id,
        )

    async def add_synthesis_to_history(
        self, room_id: str, synthesis_text: str, trajectory: Any | None = None
    ) -> str | None:
        return await projection.add_synthesis_to_history(
            repository=self.memory_repository,
            room_id=room_id,
            synthesis_text=synthesis_text,
            trajectory=trajectory,
            id_factory=self.id_factory,
            now=self.now,
            llm_provider=self.llm_provider,
            llm_config=self.llm_config,
            background_task_runner=self.background_task_runner,
        )

    async def update_room_summary(
        self,
        room_id: str,
        synthesis_text: str,
        synthesis_turn_id: str | None = None,
    ) -> bool:
        return await summary.update_room_summary(
            repository=self.memory_repository,
            llm_provider=self.llm_provider,
            llm_config=self.llm_config,
            room_id=room_id,
            synthesis_text=synthesis_text,
            synthesis_turn_id=synthesis_turn_id,
            id_factory=self.id_factory,
            now=self.now,
        )

    async def legacy_search(
        self,
        query: str,
        room_id: str,
        user_id: str | None = None,
        limit: int | None = None,
    ) -> dict:
        _results, response = await search.search_memory(
            room_id=room_id,
            query=query,
            limit=limit if limit is not None else self.search_config.max_results,
            vector=self.vector,
            llm_provider=self.llm_provider,
            content_repository=self.content_repository,
            config=self.search_config,
        )
        response["user_id"] = user_id
        return response

    async def should_compact(self, room_id: str) -> bool:
        return await compaction.should_compact(
            self.memory_repository, room_id, self.compaction_config
        )

    async def compact_if_needed(self, room_id: str):
        return await compaction.compact_if_needed(
            repository=self.memory_repository,
            content_repository=self.content_repository,
            room_id=room_id,
            config=self.compaction_config,
            now=self.now,
            index_turn=self.index_turn_for_search,
        )

    async def compact_room_memory(
        self, room_id: str, room_memory_doc: dict | None = None
    ):
        return await compaction.compact_room_memory(
            repository=self.memory_repository,
            content_repository=self.content_repository,
            room_id=room_id,
            room_memory_doc=room_memory_doc,
            config=self.compaction_config,
            now=self.now,
            index_turn=self.index_turn_for_search,
        )

    async def expand_turn_content(self, room_id: str, turn_id: str) -> str | None:
        return await compaction.expand_turn_content(
            self.memory_repository,
            self.content_repository,
            room_id,
            turn_id,
            now=self.now(),
        )

    async def expand_turn_content_from_turn(self, turn_doc: dict) -> str:
        return await compaction.expand_turn_content_from_turn(
            self.content_repository,
            turn_doc,
            now=self.now(),
        )

    async def fetch_turn_content(self, turn_id: str, room_id: str) -> str:
        return await compaction.fetch_turn_content(
            self.memory_repository,
            self.content_repository,
            turn_id=turn_id,
            room_id=room_id,
        )

    async def get_compaction_stats(self, room_id: str) -> dict:
        return await compaction.get_compaction_stats(
            self.memory_repository,
            self.content_repository,
            room_id,
        )

    async def index_turn_for_search(self, room_id: str, turn_doc: dict) -> bool:
        return await search.index_turn_for_search(
            room_id=room_id,
            turn_doc=turn_doc,
            vector=self.vector,
            llm_provider=self.llm_provider,
            config=self.search_config,
        )

    async def delete_room_index(self, room_id: str) -> bool:
        return await search.delete_room_index(
            room_id=room_id,
            vector=self.vector,
            config=self.search_config,
        )

    async def content_upsert_full_content(
        self,
        room_id: str,
        turn_id: str,
        content: str,
        content_type: str,
        turn_notes: dict | None = None,
    ) -> str:
        return await store_full_content(
            self.content_repository,
            room_id=room_id,
            turn_id=turn_id,
            content=content,
            content_type=content_type,
            turn_notes=turn_notes,
            now=self.now(),
            config=self.compaction_config,
        )

    async def content_get_content_by_document_id(
        self, document_id: str
    ) -> str | None:
        doc = await self.content_repository.get_content_by_document_id(document_id)
        return content_from_document(doc, now=self.now())

    async def content_get_content_by_turn_id(
        self, room_id: str, turn_id: str
    ) -> str | None:
        doc = await self.content_repository.get_content_by_turn_id(room_id, turn_id)
        return content_from_document(doc, now=self.now())

    async def content_expand_mongodb_reference(
        self, content_ref: dict, turn_id: str
    ) -> str:
        return await expand_mongodb_reference(
            self.content_repository, content_ref, turn_id, now=self.now()
        )

    async def content_delete_content_by_turn_id(
        self, room_id: str, turn_id: str
    ) -> bool:
        return await self.content_repository.delete_content_by_turn_id(room_id, turn_id)

    async def content_delete_content_by_room_id(self, room_id: str) -> int:
        return await self.content_repository.delete_content_by_room_id(room_id)

    async def content_get_content_stats_for_room(self, room_id: str) -> dict:
        return await self.content_repository.get_content_stats_for_room(room_id)


def _background_task(coro: Awaitable[Any]) -> None:
    task = asyncio.create_task(coro)
    task.add_done_callback(_log_background_task_exception)


def _log_background_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        logger.exception("Context memory background task failed")
