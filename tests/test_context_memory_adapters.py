from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app_shell.compaction_service import CompactionService
from app_shell.memory_search_service import MemorySearchService
from app_shell.memory_service import RoomMemoryService
from common.dto import AssembledContext, CompactionResult, MemorySearchResult
from context_memory import ContextMemoryFacade
from context_memory.compat.context_assembly import ContextAssemblyService
from context_memory.config import CompactionConfig, MemorySearchConfig
from models.memory import ConversationTurn, RoomMemory, TurnRole
from models.request import RoomCenterMemoryRequest

NOW = datetime(2026, 5, 13, tzinfo=UTC)


class FakeFacade:
    def __init__(self):
        self.calls = []
        self.created_doc = None
        self.doc = None

    def assemble_supervisor_context_from_memory(self, room_memory_doc, current_task, **kwargs):
        self.calls.append(("assemble_supervisor", room_memory_doc, current_task, kwargs))
        return assembled("supervisor context", mode="supervisor")

    def assemble_agent_execution_context_from_memory(self, room_memory_doc, current_task, **kwargs):
        self.calls.append(("assemble_agent", room_memory_doc, current_task, kwargs))
        return assembled("agent context", mode="agent")

    async def should_compact(self, room_id: str) -> bool:
        self.calls.append(("should_compact", room_id))
        return True

    async def compact_if_needed(self, room_id: str):
        self.calls.append(("compact_if_needed", room_id))
        return CompactionResult(room_id=room_id, compacted_count=1, tokens_saved=10)

    async def legacy_search(
        self,
        query: str,
        room_id: str,
        user_id: str | None = None,
        limit: int | None = None,
    ):
        self.calls.append(("legacy_search", query, room_id, user_id, limit))
        return {
            "query": query,
            "room_id": room_id,
            "results": [
                MemorySearchResult(
                    room_id=room_id,
                    content="result",
                    score=0.9,
                    metadata={"turn_id": "t1", "source_type": "turn"},
                )
            ],
            "total_matches": 1,
            "search_time_ms": 1.0,
            "searched_at": NOW,
        }

    async def index_turn_for_search(self, room_id: str, turn_doc: dict) -> bool:
        self.calls.append(("index_turn_for_search", room_id, turn_doc["turn_id"]))
        return True

    async def legacy_create_room_memory(self, memory_doc: dict) -> dict:
        self.calls.append(("legacy_create_room_memory", memory_doc["room_id"]))
        self.created_doc = memory_doc
        self.doc = memory_doc
        memory_doc.setdefault("memory_id", "m1")
        memory_doc.setdefault("memory_content", {"conversation_history": []})
        memory_doc.setdefault("conversation_history", [])
        memory_doc.setdefault("room_summary", {})
        memory_doc.setdefault("room_facts", [])
        memory_doc.setdefault("memory_created_at", NOW)
        return memory_doc

    async def legacy_get_room_memory_by_memory_id(self, memory_id: str):
        if self.doc and self.doc.get("memory_id") == memory_id:
            return self.doc
        return None

    async def legacy_update_room_memory_by_memory_id(
        self, memory_id: str, updates: dict
    ) -> bool:
        self.calls.append(("legacy_update_room_memory_by_memory_id", memory_id, updates))
        if not self.doc or self.doc.get("memory_id") != memory_id:
            return False
        self.doc.update(updates)
        return True

    async def content_upsert_full_content(self, **kwargs) -> str:
        self.calls.append(("content_upsert_full_content", kwargs["room_id"], kwargs["turn_id"]))
        return "doc1"


class RaisingRoomMemoryFacade(FakeFacade):
    async def legacy_create_room_memory(self, memory_doc: dict) -> dict:
        raise RuntimeError("facade write failed")

    async def legacy_get_room_memory_by_room_id(self, room_id: str):
        raise RuntimeError("facade read failed")

    async def legacy_get_room_memory_by_memory_id(self, memory_id: str):
        raise RuntimeError("facade memory-id read failed")

    async def legacy_update_room_memory_by_room_id(self, room_id: str, doc: dict):
        raise RuntimeError("facade update failed")

    async def legacy_update_room_memory_by_memory_id(self, memory_id: str, doc: dict):
        raise RuntimeError("facade memory-id update failed")

    async def legacy_delete_room_memory_by_memory_id(self, memory_id: str):
        raise RuntimeError("facade memory-id delete failed")

    async def initialize_or_update_room_memory(self, *args, **kwargs):
        raise RuntimeError("facade projection failed")


class DuplicateAgentResponseFacade(FakeFacade):
    async def add_agent_response_to_memory(self, *args, **kwargs):
        self.calls.append(("add_agent_response_to_memory", kwargs.get("message_id")))
        return False, True


class DuplicateUserProjectionFacade(FakeFacade):
    async def initialize_or_update_room_memory(self, *args, **kwargs):
        self.calls.append(("initialize_or_update_room_memory", kwargs.get("message_id")))
        return {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {"conversation_history": []},
            "conversation_history": [],
            "room_summary": {},
            "room_facts": [],
            "memory_created_at": NOW,
            "_context_memory_duplicate_turn": True,
        }


class FailedUserProjectionFacade(FakeFacade):
    async def initialize_or_update_room_memory(self, *args, **kwargs):
        self.calls.append(("initialize_or_update_room_memory", kwargs.get("message_id")))
        return None


class RealFacadeMemoryRepository:
    def __init__(self, doc: dict | None = None):
        self.doc = doc
        self.compacted_entries: list[dict] = []

    async def get_room_memory(self, room_id: str) -> dict | None:
        if self.doc and self.doc.get("room_id") == room_id:
            return self.doc
        return None

    async def upsert_room_memory(self, room_id: str, memory: dict) -> None:
        self.doc = {**memory, "room_id": room_id}

    async def get_user_memories(self, user_id: str) -> list[dict]:
        return []

    async def delete_room_memory(self, room_id: str) -> bool:
        self.doc = None
        return True

    async def create_room_memory(self, memory: dict) -> str:
        self.doc = dict(memory)
        return memory["memory_id"]

    async def ensure_room_memory(self, room_id: str, defaults: dict) -> dict:
        if self.doc is None:
            self.doc = {"room_id": room_id, **defaults}
        return self.doc

    async def get_room_memory_by_memory_id(self, memory_id: str) -> dict | None:
        if self.doc and self.doc.get("memory_id") == memory_id:
            return self.doc
        return None

    async def update_room_memory_by_room_id(self, room_id: str, updates: dict) -> bool:
        if not self.doc or self.doc.get("room_id") != room_id:
            return False
        self.doc.update(updates)
        return True

    async def update_room_memory_by_memory_id(self, memory_id: str, updates: dict) -> bool:
        if not self.doc or self.doc.get("memory_id") != memory_id:
            return False
        self.doc.update(updates)
        return True

    async def legacy_update_room_memory_by_memory_id(
        self, memory_id: str, updates: dict
    ) -> bool:
        self.calls.append(("legacy_update_room_memory_by_memory_id", memory_id, updates))
        return await self.update_room_memory_by_memory_id(memory_id, updates)

    async def delete_room_memory_by_memory_id(self, memory_id: str) -> bool:
        self.doc = None
        return True

    async def push_and_trim_conversation_turn(self, *args, **kwargs):
        return True, True

    async def push_and_trim_conversation_turn_if_absent(self, *args, **kwargs):
        return True, True, False

    async def update_turn_notes(self, room_id: str, turn_id: str, turn_notes: dict) -> bool:
        return True

    async def get_room_summary_projection(self, room_id: str) -> dict | None:
        return self.doc

    async def update_room_summary_atomic(self, *args, **kwargs) -> bool:
        return True

    async def compact_turns_bulk(self, room_id: str, compacted_turns: list[dict]) -> bool:
        self.compacted_entries.extend(compacted_turns)
        return True

    async def list_room_ids_with_memory(self, limit: int | None = None) -> list[str]:
        return [self.doc["room_id"]] if self.doc else []


class RealFacadeContentRepository:
    def __init__(self, *, text_results: list[dict] | None = None):
        self.text_results = text_results or []
        self.stored: list[dict] = []

    async def upsert_full_content(self, **kwargs) -> str:
        self.stored.append(kwargs)
        return kwargs["document_id"]

    async def get_content_by_document_id(self, document_id: str) -> dict | None:
        for doc in self.stored:
            if doc.get("document_id") == document_id:
                return doc
        return None

    async def get_content_by_turn_id(self, room_id: str, turn_id: str) -> dict | None:
        for doc in self.stored:
            if doc.get("room_id") == room_id and doc.get("turn_id") == turn_id:
                return doc
        return None

    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool:
        return True

    async def delete_content_by_room_id(self, room_id: str) -> int:
        return 0

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        return {"room_id": room_id, "total_documents": len(self.stored)}

    async def text_search(self, room_id: str, query: str, limit: int = 50) -> list[dict]:
        return self.text_results[:limit]

    async def hydrate_turn_notes(self, room_id: str, turn_ids: list[str]) -> list[dict]:
        return []


class RealFacadeRoomHistoryReader:
    async def get_messages_for_room(self, room_id: str, limit: int, before=None):
        return []

    async def get_messages_by_ids(self, message_ids: list[str]):
        return []

    async def get_message_thread(self, parent_message_id: str):
        return []


class RealFacadeVector:
    def __init__(self):
        self.upserted: list[tuple[str, list]] = []
        self.deleted: list[tuple[str, dict]] = []

    async def search(self, index, vector, top_k, filter=None):
        return []

    async def upsert(self, index, records):
        self.upserted.append((index, records))

    async def delete_by_filter(self, index, filter):
        self.deleted.append((index, filter))


class RealFacadeLLM:
    async def generate(self, request):
        return None

    async def generate_structured(self, messages, schema, model=None):
        return type("Response", (), {"data": {}})()

    async def embed(self, text):
        return [0.1, 0.2]

    async def embed_batch(self, texts):
        return [[0.1, 0.2] for _ in texts]


def real_context_memory_facade(
    *,
    memory_repository: RealFacadeMemoryRepository | None = None,
    content_repository: RealFacadeContentRepository | None = None,
    search_config: MemorySearchConfig | None = None,
    compaction_config: CompactionConfig | None = None,
):
    return ContextMemoryFacade(
        memory_repository=memory_repository or RealFacadeMemoryRepository(),
        content_repository=content_repository or RealFacadeContentRepository(),
        room_history_reader=RealFacadeRoomHistoryReader(),
        vector=RealFacadeVector(),
        llm_provider=RealFacadeLLM(),
        id_factory=lambda: "id-1",
        now=lambda: NOW,
        search_config=search_config or MemorySearchConfig(enabled=True),
        compaction_config=compaction_config or CompactionConfig(enabled=True),
    )


def assembled(context: str, *, mode: str):
    return AssembledContext(
        room_id="r1",
        total_tokens=3,
        metadata={
            "context": context,
            "occupancy_pct": 1.0,
            "was_truncated": False,
            "truncation_reason": None,
            "turns_included": 1,
            "turns_truncated": 0,
            "stable_prefix_tokens": 1,
            "dynamic_suffix_tokens": 2,
            "full_turns": 1,
            "compact_turns": 0,
            "mode": mode,
        },
    )


def truncated_assembled(context: str, *, mode: str):
    result = assembled(context, mode=mode)
    metadata = dict(result.metadata)
    metadata.update(
        {
            "was_truncated": True,
            "truncation_reason": "token_budget_exceeded",
            "turns_truncated": 2,
        }
    )
    return result.model_copy(update={"metadata": metadata})


def room_memory():
    return RoomMemory(
        room_id="r1",
        memory_id="m1",
        conversation_history=[
            ConversationTurn(role=TurnRole.USER, content="hello", turn_id="t1")
        ],
    )


def test_context_assembly_service_delegates_supervisor():
    facade = FakeFacade()
    service = ContextAssemblyService()
    service.bind_facade(facade)

    result = service.build_supervisor_context(room_memory(), "task")

    assert result.context == "supervisor context"
    assert facade.calls[0][0] == "assemble_supervisor"


def test_context_assembly_service_delegates_agent():
    facade = FakeFacade()
    service = ContextAssemblyService()
    service.bind_facade(facade)

    result = service.build_agent_execution_context(room_memory(), "task", agent_name="Agent")

    assert result.context == "agent context"
    assert facade.calls[0][0] == "assemble_agent"


@pytest.mark.asyncio
async def test_compaction_service_delegates_should_compact():
    facade = FakeFacade()
    service = CompactionService()
    service.bind_facade(facade)

    assert await service.should_compact("r1") is True
    assert facade.calls == [("should_compact", "r1")]


@pytest.mark.asyncio
async def test_compaction_service_delegates_compact_if_needed():
    facade = FakeFacade()
    service = CompactionService()
    service.bind_facade(facade)

    result = await service.compact_if_needed("r1")

    assert result.compacted_count == 1
    assert facade.calls == [("compact_if_needed", "r1")]


@pytest.mark.asyncio
async def test_compaction_service_exercises_real_facade_compaction_path():
    memory_repository = RealFacadeMemoryRepository(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "memory_content": {
                "conversation_history": [
                    {
                        "turn_id": "t1",
                        "role": "user",
                        "content": "old content",
                        "representation": "full",
                        "content_type": "text",
                        "estimated_tokens_full": 40,
                        "estimated_tokens_compact": 5,
                    }
                ]
            },
            "conversation_history": [
                {
                    "turn_id": "t1",
                    "role": "user",
                    "content": "old content",
                    "representation": "full",
                    "content_type": "text",
                    "estimated_tokens_full": 40,
                    "estimated_tokens_compact": 5,
                }
            ],
        }
    )
    content_repository = RealFacadeContentRepository()
    service = CompactionService()
    service.bind_facade(
        real_context_memory_facade(
            memory_repository=memory_repository,
            content_repository=content_repository,
            search_config=MemorySearchConfig(enabled=False),
            compaction_config=CompactionConfig(
                enabled=True,
                max_full_turns=0,
                max_total_tokens=1,
                preserve_recent_turns=0,
                content_ttl_days=0,
                concurrency=1,
            ),
        )
    )

    result = await service.compact_room_memory("r1")

    assert result.compacted_count == 1
    assert content_repository.stored[0]["turn_id"] == "t1"
    assert memory_repository.compacted_entries[0]["turn_id"] == "t1"


@pytest.mark.asyncio
async def test_memory_search_service_delegates_search():
    facade = FakeFacade()
    service = MemorySearchService()
    service.bind_facade(facade)

    result = await service.search("query", "r1", user_id="u1")

    assert result.results[0].content == "result"
    assert facade.calls == [("legacy_search", "query", "r1", "u1", service.config.max_results)]


@pytest.mark.asyncio
async def test_memory_search_service_exercises_real_facade_search_path():
    content_repository = RealFacadeContentRepository(
        text_results=[
            {
                "turn_id": "t1",
                "score": 1.0,
                "turn_notes": {"one_liner": "real facade result"},
                "content_type": "text",
                "stored_at": NOW,
            }
        ]
    )
    service = MemorySearchService()
    service.bind_facade(
        real_context_memory_facade(
            content_repository=content_repository,
            search_config=MemorySearchConfig(
                enabled=True,
                temporal_decay_enabled=False,
                max_results=5,
            ),
        )
    )

    result = await service.search("query", "r1")

    assert result.results[0].content == "real facade result"
    assert result.keyword_search_used is True


@pytest.mark.asyncio
async def test_memory_search_service_real_facade_respects_configured_max_results():
    content_repository = RealFacadeContentRepository(
        text_results=[
            {
                "turn_id": f"t{index}",
                "score": 1.0,
                "turn_notes": {"one_liner": f"result {index}"},
                "content_type": "text",
                "stored_at": NOW,
            }
            for index in range(5)
        ]
    )
    service = MemorySearchService()
    service.bind_facade(
        real_context_memory_facade(
            content_repository=content_repository,
            search_config=MemorySearchConfig(
                enabled=True,
                temporal_decay_enabled=False,
                max_results=3,
            ),
        )
    )

    result = await service.search("query", "r1")

    assert len(result.results) == 3


@pytest.mark.asyncio
async def test_memory_search_service_delegates_index():
    facade = FakeFacade()
    service = MemorySearchService()
    service.bind_facade(facade)

    ok = await service.index_turn_for_search(
        ConversationTurn(role=TurnRole.USER, content="hello", turn_id="t1"),
        "r1",
    )

    assert ok is True
    assert facade.calls == [("index_turn_for_search", "r1", "t1")]


@pytest.mark.asyncio
async def test_room_memory_service_delegates_create():
    facade = FakeFacade()
    service = RoomMemoryService()
    service.bind_facade(facade)

    response = await service.create_room_memory(
        RoomCenterMemoryRequest(room_id="r1", memory_id="m1")
    )

    assert response.success is True
    assert response.memory.memory_id == "m1"
    assert facade.calls == [("legacy_create_room_memory", "r1")]


@pytest.mark.asyncio
async def test_room_memory_service_delegates_create_with_initial_content():
    facade = FakeFacade()
    service = RoomMemoryService()
    service.bind_facade(facade)

    response = await service.create_room_memory(
        RoomCenterMemoryRequest(
            room_id="r1",
            memory_id="m1",
            memory_content="initial user request",
        )
    )

    assert response.success is True
    assert response.memory.get_conversation_history()[0].content == "initial user request"
    assert facade.created_doc["memory_content"]["conversation_history"][0]["content"] == "initial user request"


@pytest.mark.asyncio
async def test_room_memory_service_create_translates_facade_exception():
    service = RoomMemoryService()
    service.bind_facade(RaisingRoomMemoryFacade())

    response = await service.create_room_memory(RoomCenterMemoryRequest(room_id="r1"))

    assert response.success is False
    assert response.status_code == 500
    assert response.error == "facade write failed"


@pytest.mark.asyncio
async def test_room_memory_service_get_translates_facade_exception():
    service = RoomMemoryService()
    service.bind_facade(RaisingRoomMemoryFacade())

    response = await service.get_room_memory_by_room_id(
        RoomCenterMemoryRequest(room_id="r1")
    )

    assert response.success is False
    assert response.status_code == 500
    assert response.error == "facade read failed"


@pytest.mark.asyncio
async def test_room_memory_service_update_translates_facade_exception():
    service = RoomMemoryService()
    service.bind_facade(RaisingRoomMemoryFacade())

    response = await service.update_room_memory_by_room_id(
        RoomCenterMemoryRequest(room_id="r1", memory=room_memory())
    )

    assert response.success is False
    assert response.status_code == 500
    assert response.error == "facade update failed"


@pytest.mark.asyncio
async def test_room_memory_service_get_by_memory_id_translates_facade_exception():
    service = RoomMemoryService()
    service.bind_facade(RaisingRoomMemoryFacade())

    response = await service.get_room_memory_by_memory_id(
        RoomCenterMemoryRequest(memory_id="m1")
    )

    assert response.success is False
    assert response.status_code == 500
    assert response.error == "facade memory-id read failed"


@pytest.mark.asyncio
async def test_room_memory_service_get_by_memory_id_treats_malformed_doc_as_not_found():
    class MalformedFacade(FakeFacade):
        async def legacy_get_room_memory_by_memory_id(self, memory_id: str):
            return {"memory_id": memory_id}

    service = RoomMemoryService()
    service.bind_facade(MalformedFacade())

    response = await service.get_room_memory_by_memory_id(
        RoomCenterMemoryRequest(memory_id="m1")
    )

    assert response.success is False
    assert response.status_code == 404
    assert response.error == "Room memory not found"


@pytest.mark.asyncio
async def test_room_memory_service_update_by_memory_id_persists_request_memory():
    facade = FakeFacade()
    facade.doc = {
        "room_id": "r1",
        "memory_id": "m1",
        "conversation_history": [],
    }
    service = RoomMemoryService()
    service.bind_facade(facade)
    updated = room_memory().model_copy(update={"conversation_history": []})

    response = await service.update_room_memory_by_memory_id(
        RoomCenterMemoryRequest(memory_id="m1", room_id="r1", memory=updated)
    )

    assert response.success is True
    assert response.memory == updated
    assert facade.calls[-1][0] == "legacy_update_room_memory_by_memory_id"
    assert facade.doc["memory_id"] == "m1"
    assert facade.doc["room_id"] == "r1"


@pytest.mark.asyncio
async def test_room_memory_service_update_by_memory_id_translates_facade_exception():
    service = RoomMemoryService()
    service.bind_facade(RaisingRoomMemoryFacade())

    response = await service.update_room_memory_by_memory_id(
        RoomCenterMemoryRequest(memory_id="m1", memory=room_memory())
    )

    assert response.success is False
    assert response.status_code == 500
    assert response.error == "facade memory-id update failed"


@pytest.mark.asyncio
async def test_room_memory_service_delete_by_memory_id_translates_facade_exception():
    service = RoomMemoryService()
    service.bind_facade(RaisingRoomMemoryFacade())

    response = await service.delete_room_memory_by_memory_id(
        RoomCenterMemoryRequest(memory_id="m1")
    )

    assert response.success is False
    assert response.status_code == 500
    assert response.error == "facade memory-id delete failed"


@pytest.mark.asyncio
async def test_room_memory_service_initialize_translates_facade_exception():
    service = RoomMemoryService()
    service.bind_facade(RaisingRoomMemoryFacade())

    response = await service.initialize_or_update_room_memory(
        RoomCenterMemoryRequest(room_id="r1", memory_content="hello")
    )

    assert response.success is False
    assert response.status_code == 500
    assert response.error == "facade projection failed"


@pytest.mark.asyncio
async def test_room_memory_service_initialize_duplicate_message_does_not_track_user():
    facade = DuplicateUserProjectionFacade()
    service = RoomMemoryService()
    service.database_service = type(
        "DB",
        (),
        {"increment_user_interactions": AsyncMock()},
    )()
    service.bind_facade(facade)

    response = await service.initialize_or_update_room_memory(
        RoomCenterMemoryRequest(
            room_id="r1",
            message_id="msg-1",
            memory_content="hello",
            user_id="u1",
        )
    )

    assert response.success is True
    service.database_service.increment_user_interactions.assert_not_awaited()


@pytest.mark.asyncio
async def test_room_memory_service_initialize_failed_write_does_not_track_user():
    facade = FailedUserProjectionFacade()
    service = RoomMemoryService()
    service.database_service = type(
        "DB",
        (),
        {"increment_user_interactions": AsyncMock()},
    )()
    service.bind_facade(facade)

    response = await service.initialize_or_update_room_memory(
        RoomCenterMemoryRequest(
            room_id="r1",
            message_id="msg-1",
            memory_content="hello",
            user_id="u1",
        )
    )

    assert response.success is False
    assert response.status_code == 500
    service.database_service.increment_user_interactions.assert_not_awaited()


@pytest.mark.asyncio
async def test_room_memory_service_agent_response_duplicate_message_is_success():
    facade = DuplicateAgentResponseFacade()
    service = RoomMemoryService()
    service.bind_facade(facade)

    response = await service.add_agent_response_to_memory(
        room_id="r1",
        agent_id="a1",
        agent_name="Agent",
        response_text="already stored",
        was_successful=True,
        message_id="agent-msg-1",
    )

    assert response.success is True
    assert response.status_code == 200
    assert response.error is None
    assert facade.calls == [("add_agent_response_to_memory", "agent-msg-1")]


def test_bound_context_assembly_updates_truncation_count():
    class TruncatedFacade(FakeFacade):
        def assemble_supervisor_context_from_memory(self, room_memory_doc, current_task, **kwargs):
            return truncated_assembled("truncated supervisor", mode="supervisor")

    service = ContextAssemblyService()
    service.bind_facade(TruncatedFacade())

    result = service.build_supervisor_context(room_memory(), "task")

    assert result.was_truncated is True
    assert service.truncation_count == 1


def test_bound_context_assembly_logs_metrics():
    facade = FakeFacade()
    service = ContextAssemblyService()
    service.bind_facade(facade)

    with patch("context_memory.legacy_assembly.record_context_metrics") as record_metrics:
        record_metrics.return_value = False
        service.build_supervisor_context(room_memory(), "task")

    record_metrics.assert_called_once()
    assert record_metrics.call_args.kwargs["room_id"] == "r1"
    assert record_metrics.call_args.kwargs["context_type"] == "supervisor"
    assert record_metrics.call_args.kwargs["metadata"]["full_turns"] == 1
    assert record_metrics.call_args.kwargs["metadata"]["compact_turns"] == 0


@pytest.mark.asyncio
async def test_services_fail_fast_before_bind():
    with pytest.raises(RuntimeError, match="ContextAssemblyService.bind_facade\\(\\) not called - startup incomplete"):
        ContextAssemblyService().build_supervisor_context(room_memory(), "task")
    with pytest.raises(RuntimeError, match="CompactionService.bind_facade\\(\\) not called - startup incomplete"):
        await CompactionService().should_compact("r1")
    with pytest.raises(RuntimeError, match="MemorySearchService.bind_facade\\(\\) not called - startup incomplete"):
        await MemorySearchService().search("query", "r1")
    with pytest.raises(RuntimeError, match="RoomMemoryService.bind_facade\\(\\) not called - startup incomplete"):
        await RoomMemoryService().create_room_memory(RoomCenterMemoryRequest(room_id="r1"))
    with pytest.raises(RuntimeError, match="CompactionService.bind_content_storage\\(\\) not called - startup incomplete"):
        await CompactionService().content_storage.upsert_full_content(
            "r1", "t1", "hello", "text"
        )
