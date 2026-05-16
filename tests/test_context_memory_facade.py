from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from common.errors import VectorIndexUnavailableError
from context_memory.config import CompactionConfig, MemorySearchConfig, TokenBudgetConfig
from context_memory.facade import ContextMemoryFacade
from context_memory.projection import new_room_memory_doc
from context_memory.translators import room_memory_info_from_doc


NOW = datetime(2026, 5, 13, tzinfo=timezone.utc)


def now():
    return NOW


class StateMemoryRepository:
    def __init__(self, doc: dict | None = None, *, fail_delete: bool = False):
        self.doc = doc
        self.fail_delete = fail_delete
        self.deleted = []
        self.user_docs = []
        self.created = []
        self.updated = []
        self.compacted = []

    async def get_room_memory(self, room_id: str) -> dict | None:
        return self.doc if self.doc and self.doc.get("room_id") == room_id else None

    async def upsert_room_memory(self, room_id: str, memory: dict) -> None:
        self.doc = {"room_id": room_id, **memory}

    async def get_user_memories(self, user_id: str) -> list[dict]:
        return [doc for doc in self.user_docs if doc["user_id"] == user_id]

    async def delete_room_memory(self, room_id: str) -> bool:
        self.deleted.append(room_id)
        if self.fail_delete:
            return False
        existed = self.doc is not None
        self.doc = None
        return existed

    async def create_room_memory(self, memory: dict) -> str:
        self.doc = memory
        self.created.append(memory)
        return memory["memory_id"]

    async def ensure_room_memory(self, room_id: str, defaults: dict) -> dict:
        if self.doc is None:
            self.doc = defaults
        return self.doc

    async def get_room_memory_by_memory_id(self, memory_id: str) -> dict | None:
        return self.doc if self.doc and self.doc.get("memory_id") == memory_id else None

    async def update_room_memory_by_room_id(self, room_id: str, updates: dict) -> bool:
        self.updated.append((room_id, updates))
        if self.doc and self.doc["room_id"] == room_id:
            self.doc.update(updates)
            return True
        return False

    async def update_room_memory_by_memory_id(self, memory_id: str, updates: dict) -> bool:
        if self.doc and self.doc["memory_id"] == memory_id:
            self.doc.update(updates)
            return True
        return False

    async def delete_room_memory_by_memory_id(self, memory_id: str) -> bool:
        if self.doc and self.doc["memory_id"] == memory_id:
            self.doc = None
            return True
        return False

    async def push_and_trim_conversation_turn(self, room_id: str, turn: dict, **kwargs):
        if not self.doc:
            return False, False
        self.doc.setdefault("conversation_history", []).append(turn)
        self.doc.setdefault("memory_content", {}).setdefault("conversation_history", []).append(turn)
        return True, True

    async def push_and_trim_conversation_turn_if_absent(self, room_id: str, turn: dict, **kwargs):
        if not self.doc:
            return False, False, False
        self.doc.setdefault("conversation_history", []).append(turn)
        self.doc.setdefault("memory_content", {}).setdefault("conversation_history", []).append(turn)
        return True, True, False

    async def update_turn_notes(self, room_id: str, turn_id: str, turn_notes: dict) -> bool:
        return True

    async def get_room_summary_projection(self, room_id: str) -> dict | None:
        return self.doc

    async def update_room_summary_atomic(self, room_id: str, room_summary: dict, **kwargs) -> bool:
        if self.doc:
            self.doc["room_summary"] = room_summary
            return True
        return False

    async def compact_turns_bulk(self, room_id: str, compacted_turns: list[dict]) -> bool:
        self.compacted.extend(compacted_turns)
        return True

    async def list_room_ids_with_memory(self, limit: int | None = None) -> list[str]:
        return [self.doc["room_id"]] if self.doc else []


class StateContentRepository:
    def __init__(self):
        self.docs = {}
        self.deleted_rooms = []
        self.raise_on_delete_room = False

    async def upsert_full_content(self, **kwargs) -> str:
        self.docs[kwargs["document_id"]] = kwargs
        return kwargs["document_id"]

    async def get_content_by_document_id(self, document_id: str) -> dict | None:
        return self.docs.get(document_id)

    async def get_content_by_turn_id(self, room_id: str, turn_id: str) -> dict | None:
        return next(
            (
                doc
                for doc in self.docs.values()
                if doc["room_id"] == room_id and doc["turn_id"] == turn_id
            ),
            None,
        )

    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool:
        doc = await self.get_content_by_turn_id(room_id, turn_id)
        if not doc:
            return False
        self.docs.pop(doc["document_id"])
        return True

    async def delete_content_by_room_id(self, room_id: str) -> int:
        if self.raise_on_delete_room:
            raise RuntimeError("content cleanup failed")
        self.deleted_rooms.append(room_id)
        keys = [key for key, doc in self.docs.items() if doc["room_id"] == room_id]
        for key in keys:
            self.docs.pop(key)
        return len(keys)

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        return {"room_id": room_id, "total_documents": len(self.docs)}

    async def text_search(self, room_id: str, query: str, limit: int = 50) -> list[dict]:
        return [
            {
                "turn_id": "t1",
                "score": 1.0,
                "turn_notes": {"one_liner": "matched"},
                "content": "matched",
                "content_type": "text",
                "stored_at": NOW,
            }
        ]

    async def hydrate_turn_notes(self, room_id: str, turn_ids: list[str]) -> list[dict]:
        return []


class FakeHistoryReader:
    def __init__(self, messages=None):
        self.messages = messages or []

    async def get_messages_by_ids(self, message_ids: list[str]):
        return [message for message in self.messages if message.message_id in message_ids]


class FakeVector:
    def __init__(self, *, raise_on_delete: bool = False, unavailable_on_delete: bool = False):
        self.deleted = []
        self.raise_on_delete = raise_on_delete
        self.unavailable_on_delete = unavailable_on_delete

    async def search(self, index, vector, top_k, filter=None):
        return []

    async def upsert(self, index, records):
        return None

    async def delete_by_filter(self, index, filter):
        if self.unavailable_on_delete:
            raise VectorIndexUnavailableError(index, "delete")
        if self.raise_on_delete:
            raise RuntimeError("vector cleanup failed")
        self.deleted.append((index, filter))


class FakeLLM:
    async def embed(self, text: str):
        return [0.1]

    async def generate_structured(self, messages, schema, model=None):
        return SimpleNamespace(data={})


class RaisingLLM(FakeLLM):
    async def generate_structured(self, messages, schema, model=None):
        raise RuntimeError("summary llm failed")


class NonDictLLM(FakeLLM):
    async def generate_structured(self, messages, schema, model=None):
        return SimpleNamespace(data="not a dict")


class MissingSummaryProjectionRepository(StateMemoryRepository):
    async def get_room_summary_projection(self, room_id: str) -> dict | None:
        return None


class FailingSummaryWriteRepository(StateMemoryRepository):
    async def update_room_summary_atomic(self, room_id: str, room_summary: dict, **kwargs) -> bool:
        return False


def facade(memory_repo=None, content_repo=None, history=None, vector=None, **overrides):
    llm_provider = overrides.pop("llm_provider", FakeLLM())
    return ContextMemoryFacade(
        memory_repository=memory_repo or StateMemoryRepository(),
        content_repository=content_repo or StateContentRepository(),
        room_history_reader=history or FakeHistoryReader(),
        vector=vector or FakeVector(),
        llm_provider=llm_provider,
        id_factory=lambda: "id-1",
        now=now,
        token_budget=TokenBudgetConfig(model_context_window=300, system_prompt=10, tool_schemas=10, response_reserve=10),
        compaction_config=CompactionConfig(enabled=True, max_full_turns=0, max_total_tokens=1, preserve_recent_turns=0, content_ttl_days=0, concurrency=1),
        search_config=MemorySearchConfig(enabled=True, temporal_decay_enabled=False, index_name="memory"),
        **overrides,
    )


def test_token_budget_with_model_window_preserves_small_caller_budget():
    budget = TokenBudgetConfig(
        model_context_window=128000,
        system_prompt=2000,
        tool_schemas=3000,
        response_reserve=4000,
    )

    scoped = budget.with_model_window(5000)

    assert scoped.model_context_window == 5000


def existing_doc():
    doc = new_room_memory_doc(room_id="r1", memory_id="m1", now=NOW)
    doc["conversation_history"] = [
        {"turn_id": "message:m1", "role": "user", "content": "hello", "estimated_tokens_full": 2}
    ]
    doc["memory_content"]["conversation_history"] = doc["conversation_history"]
    doc["room_summary"]["current_goal"] = "Finish tests"
    return doc


@pytest.mark.asyncio
async def test_facade_assemble_context_creates_transient_doc():
    service = facade(
        history=FakeHistoryReader(
            [SimpleNamespace(message_id="m1", room_id="r1", content="transient task")]
        )
    )

    result = await service.assemble_context("r1", "m1", token_budget=300)

    assert result.room_id == "r1"
    assert "transient task" in result.metadata["context"]
    assert result.metadata["message_id"] == "m1"


@pytest.mark.asyncio
async def test_facade_assemble_context_with_existing_memory():
    service = facade(memory_repo=StateMemoryRepository(existing_doc()))

    result = await service.assemble_context("r1", "m1", token_budget=300)

    assert "Finish tests" in result.metadata["context"]
    assert "hello" in result.metadata["context"]


@pytest.mark.asyncio
async def test_facade_assemble_context_agent_path_does_not_use_agent_id_as_name():
    service = facade(memory_repo=StateMemoryRepository(existing_doc()))

    result = await service.assemble_context(
        "r1",
        "m1",
        token_budget=300,
        agent_id="agent-1",
    )

    assert result.metadata["agent_id"] == "agent-1"
    assert "You are agent-1" not in result.metadata["context"]


@pytest.mark.asyncio
async def test_facade_assemble_context_zero_token_budget_does_not_crash():
    service = facade(memory_repo=StateMemoryRepository(existing_doc()))

    result = await service.assemble_context("r1", "m1", token_budget=0)

    assert result.room_id == "r1"
    assert result.total_tokens >= 0
    assert "hello" in result.metadata["context"]


@pytest.mark.asyncio
async def test_facade_get_room_memory():
    info = await facade(memory_repo=StateMemoryRepository(existing_doc())).get_room_memory("r1")

    assert info.room_id == "r1"
    assert info.memory_id == "m1"
    assert "Summary: Finish tests" in info.content


def test_room_memory_info_estimates_legacy_full_turn_tokens():
    info = room_memory_info_from_doc(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "conversation_history": [
                {
                    "turn_id": "t1",
                    "role": "user",
                    "representation": "full",
                    "content": "legacy content with missing token estimate",
                    "estimated_tokens_full": 0,
                }
            ],
        }
    )

    assert info.token_count > 0


@pytest.mark.asyncio
async def test_facade_search_memory():
    results = await facade(content_repo=StateContentRepository()).search_memory("r1", "matched")

    assert results[0].content == "matched"
    assert results[0].metadata["turn_id"] == "t1"


@pytest.mark.asyncio
async def test_facade_legacy_search_limit_zero_returns_no_results():
    response = await facade(content_repo=StateContentRepository()).legacy_search(
        "matched",
        "r1",
        limit=0,
    )

    assert response["results"] == []
    assert response["total_matches"] == 1


@pytest.mark.asyncio
async def test_facade_get_user_memories():
    repo = StateMemoryRepository()
    repo.user_docs = [{"user_id": "u1", "communication_style": "direct"}]

    memories = await facade(memory_repo=repo).get_user_memories("u1")

    assert memories[0].memory_id == "user_memory:u1"
    assert memories[0].content == "Communication Style: direct"


@pytest.mark.asyncio
async def test_facade_delete_room_memory_nothing_to_delete():
    repo = StateMemoryRepository(None)
    content_repo = StateContentRepository()
    vector = FakeVector()

    assert await facade(memory_repo=repo, content_repo=content_repo, vector=vector).delete_room_memory("missing") is True
    assert repo.deleted == []
    assert content_repo.deleted_rooms == ["missing"]
    assert vector.deleted == [("memory", {"room_id": {"$eq": "missing"}})]


@pytest.mark.asyncio
async def test_facade_delete_room_memory_existing():
    repo = StateMemoryRepository(existing_doc())
    content_repo = StateContentRepository()
    vector = FakeVector()

    assert await facade(memory_repo=repo, content_repo=content_repo, vector=vector).delete_room_memory("r1")
    assert repo.deleted == ["r1"]
    assert content_repo.deleted_rooms == ["r1"]
    assert vector.deleted == [("memory", {"room_id": {"$eq": "r1"}})]


@pytest.mark.asyncio
async def test_facade_delete_room_memory_returns_false_on_content_cleanup_failure():
    content_repo = StateContentRepository()
    content_repo.raise_on_delete_room = True

    assert await facade(
        memory_repo=StateMemoryRepository(None),
        content_repo=content_repo,
    ).delete_room_memory("r1") is False


@pytest.mark.asyncio
async def test_facade_delete_room_memory_deletes_memory_before_content_cleanup_failure():
    repo = StateMemoryRepository(existing_doc())
    content_repo = StateContentRepository()
    content_repo.raise_on_delete_room = True

    assert await facade(
        memory_repo=repo,
        content_repo=content_repo,
    ).delete_room_memory("r1") is False
    assert repo.doc is None
    assert repo.deleted == ["r1"]


@pytest.mark.asyncio
async def test_facade_delete_room_memory_returns_false_on_vector_cleanup_failure():
    assert await facade(
        memory_repo=StateMemoryRepository(None),
        vector=FakeVector(raise_on_delete=True),
    ).delete_room_memory("r1") is False


@pytest.mark.asyncio
async def test_facade_delete_room_memory_deletes_memory_before_vector_cleanup_failure():
    repo = StateMemoryRepository(existing_doc())

    assert await facade(
        memory_repo=repo,
        vector=FakeVector(raise_on_delete=True),
    ).delete_room_memory("r1") is False
    assert repo.doc is None
    assert repo.deleted == ["r1"]


@pytest.mark.asyncio
async def test_facade_delete_room_memory_attempts_content_cleanup_on_vector_failure():
    repo = StateMemoryRepository(existing_doc())
    content_repo = StateContentRepository()
    await content_repo.upsert_full_content(
        room_id="r1",
        turn_id="t1",
        document_id="doc1",
        content="full content",
        content_type="text",
        content_hash="hash",
        stored_at=NOW,
        turn_notes={},
    )

    assert await facade(
        memory_repo=repo,
        content_repo=content_repo,
        vector=FakeVector(raise_on_delete=True),
    ).delete_room_memory("r1") is False
    assert content_repo.docs == {}


@pytest.mark.asyncio
async def test_facade_delete_room_memory_preserves_backing_when_memory_delete_fails():
    repo = StateMemoryRepository(existing_doc(), fail_delete=True)
    content_repo = StateContentRepository()
    vector = FakeVector()
    await content_repo.upsert_full_content(
        room_id="r1",
        turn_id="t1",
        document_id="doc1",
        content="full content",
        content_type="text",
        content_hash="hash",
        stored_at=NOW,
        turn_notes={},
    )

    assert await facade(
        memory_repo=repo,
        content_repo=content_repo,
        vector=vector,
    ).delete_room_memory("r1") is False
    assert repo.doc is not None
    assert content_repo.docs["doc1"]["content"] == "full content"
    assert vector.deleted == []


@pytest.mark.asyncio
async def test_facade_delete_room_memory_treats_vector_unavailable_as_noop():
    assert await facade(
        memory_repo=StateMemoryRepository(None),
        vector=FakeVector(unavailable_on_delete=True),
    ).delete_room_memory("r1") is True


@pytest.mark.asyncio
async def test_facade_legacy_delete_memory_id_uses_canonical_room_cleanup():
    repo = StateMemoryRepository(existing_doc())
    content_repo = StateContentRepository()
    vector = FakeVector()
    await content_repo.upsert_full_content(
        room_id="r1",
        turn_id="t1",
        document_id="doc1",
        content="full content",
        content_type="text",
        content_hash="hash",
        stored_at=NOW,
        turn_notes={},
    )

    ok = await facade(
        memory_repo=repo,
        content_repo=content_repo,
        vector=vector,
    ).legacy_delete_room_memory_by_memory_id("m1")

    assert ok is True
    assert repo.deleted == ["r1"]
    assert content_repo.docs == {}
    assert vector.deleted == [("memory", {"room_id": {"$eq": "r1"}})]


@pytest.mark.asyncio
async def test_facade_legacy_delete_room_id_uses_canonical_room_cleanup():
    repo = StateMemoryRepository(existing_doc())
    content_repo = StateContentRepository()
    vector = FakeVector()
    await content_repo.upsert_full_content(
        room_id="r1",
        turn_id="t1",
        document_id="doc1",
        content="full content",
        content_type="text",
        content_hash="hash",
        stored_at=NOW,
        turn_notes={},
    )

    ok = await facade(
        memory_repo=repo,
        content_repo=content_repo,
        vector=vector,
    ).legacy_delete_room_memory_by_room_id("r1")

    assert ok is True
    assert repo.deleted == ["r1"]
    assert content_repo.docs == {}
    assert vector.deleted == [("memory", {"room_id": {"$eq": "r1"}})]


@pytest.mark.asyncio
async def test_facade_project_message():
    repo = StateMemoryRepository(existing_doc())
    service = facade(
        memory_repo=repo,
        history=FakeHistoryReader(
            [SimpleNamespace(message_id="m2", room_id="r1", message_type="user", content="new")]
        ),
    )

    status = await service.project_message_for_event("r1", "m2")

    assert status["projected"] is True
    assert repo.doc["conversation_history"][-1]["turn_id"] == "message:m2"


@pytest.mark.asyncio
async def test_facade_run_compaction():
    doc = existing_doc()
    doc["conversation_history"][0]["estimated_tokens_full"] = 100
    repo = StateMemoryRepository(doc)
    content_repo = StateContentRepository()

    result = await facade(memory_repo=repo, content_repo=content_repo).run_compaction("r1")

    assert result.compacted_count == 1
    assert repo.compacted[0]["turn_id"] == "message:m1"


@pytest.mark.asyncio
async def test_facade_update_room_summary_logs_llm_failure(caplog):
    caplog.set_level("WARNING")

    result = await facade(
        memory_repo=StateMemoryRepository(existing_doc()),
        llm_provider=RaisingLLM(),
    ).update_room_summary("r1", "synthesis", "s1")

    assert result is False
    assert "Failed to extract room summary" in caplog.text


@pytest.mark.asyncio
async def test_facade_update_room_summary_logs_invalid_llm_payload(caplog):
    caplog.set_level("WARNING")

    result = await facade(
        memory_repo=StateMemoryRepository(existing_doc()),
        llm_provider=NonDictLLM(),
    ).update_room_summary("r1", "synthesis", "s1")

    assert result is False
    assert "Room summary extraction returned invalid payload" in caplog.text


@pytest.mark.asyncio
async def test_facade_update_room_summary_logs_missing_projection(caplog):
    caplog.set_level("WARNING")

    result = await facade(
        memory_repo=MissingSummaryProjectionRepository(existing_doc()),
    ).update_room_summary("r1", "synthesis", "s1")

    assert result is False
    assert "Room summary projection missing" in caplog.text


@pytest.mark.asyncio
async def test_facade_update_room_summary_logs_persistence_failure(caplog):
    caplog.set_level("WARNING")

    result = await facade(
        memory_repo=FailingSummaryWriteRepository(existing_doc()),
    ).update_room_summary("r1", "synthesis", "s1")

    assert result is False
    assert "Failed to persist room summary" in caplog.text


@pytest.mark.asyncio
async def test_facade_legacy_crud_helpers():
    repo = StateMemoryRepository()
    service = facade(memory_repo=repo)

    created = await service.legacy_create_room_memory({"room_id": "r1"})
    fetched = await service.legacy_get_room_memory_by_room_id("r1")
    updated = await service.legacy_update_room_memory_by_room_id("r1", {"extra": "value"})

    assert created["memory_id"] == "id-1"
    assert fetched["room_id"] == "r1"
    assert updated is True
    assert repo.doc["extra"] == "value"


@pytest.mark.asyncio
async def test_facade_content_helpers():
    content_repo = StateContentRepository()
    service = facade(content_repo=content_repo)

    document_id = await service.content_upsert_full_content(
        "r1", "t1", "stored", "text", {"one_liner": "stored"}
    )

    assert await service.content_get_content_by_document_id(document_id) == "stored"
    assert await service.content_get_content_by_turn_id("r1", "t1") == "stored"
    assert (await service.content_get_content_stats_for_room("r1"))["total_documents"] == 1
