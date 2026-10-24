from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from common.observability import NoopTracingProvider
from context_memory.config import CompactionConfig
from context_memory.facade import ContextMemoryFacade
from context_memory.projection import new_room_memory_doc

NOW = datetime(2026, 5, 13, tzinfo=UTC)


def now():
    return NOW


class StateMemoryRepository:
    def __init__(self, doc: dict | None = None, *, fail_delete: bool = False):
        self.doc = doc
        self.fail_delete = fail_delete
        self.deleted = []
        self.created = []
        self.compacted = []

    async def get_room_memory(self, conversation_id: str) -> dict | None:
        return (
            self.doc
            if self.doc and self.doc.get("room_id") == conversation_id
            else None
        )

    async def upsert_room_memory(self, conversation_id: str, memory: dict) -> None:
        self.doc = {"room_id": conversation_id, **memory}

    async def delete_room_memory(self, conversation_id: str) -> bool:
        self.deleted.append(conversation_id)
        if self.fail_delete:
            return False
        existed = self.doc is not None
        self.doc = None
        return existed

    async def create_room_memory(self, memory: dict) -> str:
        self.doc = memory
        self.created.append(memory)
        return memory["memory_id"]

    async def ensure_room_memory(self, conversation_id: str, defaults: dict) -> dict:
        if self.doc is None:
            self.doc = defaults
        return self.doc

    async def push_and_trim_conversation_turn_if_absent(
        self, conversation_id: str, turn: dict, **kwargs
    ):
        if not self.doc:
            return False, False, False
        self.doc.setdefault("conversation_history", []).append(turn)
        self.doc.setdefault("memory_content", {}).setdefault(
            "conversation_history", []
        ).append(turn)
        return True, True, False

    async def compact_turns_bulk(
        self, conversation_id: str, compacted_turns: list[dict]
    ) -> bool:
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

    async def get_content_by_turn_id(
        self, conversation_id: str, turn_id: str
    ) -> dict | None:
        return next(
            (
                doc
                for doc in self.docs.values()
                if doc["room_id"] == conversation_id and doc["turn_id"] == turn_id
            ),
            None,
        )

    async def delete_content_by_turn_id(
        self, conversation_id: str, turn_id: str
    ) -> bool:
        doc = await self.get_content_by_turn_id(conversation_id, turn_id)
        if not doc:
            return False
        self.docs.pop(doc["document_id"])
        return True

    async def delete_content_by_room_id(self, conversation_id: str) -> int:
        if self.raise_on_delete_room:
            raise RuntimeError("content cleanup failed")
        self.deleted_rooms.append(conversation_id)
        keys = [
            key for key, doc in self.docs.items() if doc["room_id"] == conversation_id
        ]
        for key in keys:
            self.docs.pop(key)
        return len(keys)


class FakeHistoryReader:
    def __init__(self, messages=None):
        self.messages = messages or []

    async def get_messages_by_ids(self, message_ids: list[str]):
        return [
            message for message in self.messages if message.message_id in message_ids
        ]


def facade(memory_repo=None, content_repo=None, history=None, **overrides):
    return ContextMemoryFacade(
        memory_repository=memory_repo or StateMemoryRepository(),
        content_repository=content_repo or StateContentRepository(),
        room_history_reader=history or FakeHistoryReader(),
        id_factory=lambda: "id-1",
        now=now,
        compaction_config=CompactionConfig(
            enabled=True,
            max_full_turns=0,
            max_total_tokens=1,
            preserve_recent_turns=0,
            content_ttl_days=0,
            concurrency=1,
        ),
        **overrides,
    )


def test_facade_uses_noop_tracer_by_default():
    service = facade()

    assert isinstance(service.tracer, NoopTracingProvider)


def existing_doc():
    doc = new_room_memory_doc(conversation_id="r1", memory_id="m1", now=NOW)
    doc["conversation_history"] = [
        {
            "turn_id": "message:m1",
            "role": "user",
            "content": "hello",
            "estimated_tokens_full": 2,
        }
    ]
    doc["memory_content"]["conversation_history"] = doc["conversation_history"]
    doc["room_summary"]["current_goal"] = "Finish tests"
    return doc


@pytest.mark.asyncio
async def test_facade_compact_if_needed_compacts_over_threshold_room():
    doc = existing_doc()
    memory_repo = StateMemoryRepository(doc)
    service = facade(memory_repo=memory_repo)

    result = await service.compact_if_needed("r1")

    assert result.conversation_id == "r1"
    assert result.compacted_count == 1
    assert memory_repo.compacted


def test_facade_does_not_expose_legacy_search():
    assert not hasattr(facade(content_repo=StateContentRepository()), "legacy_search")


@pytest.mark.asyncio
async def test_facade_delete_room_memory_nothing_to_delete():
    repo = StateMemoryRepository(None)
    content_repo = StateContentRepository()

    assert (
        await facade(memory_repo=repo, content_repo=content_repo).delete_room_memory(
            "missing"
        )
        is True
    )
    assert repo.deleted == []
    assert content_repo.deleted_rooms == ["missing"]


@pytest.mark.asyncio
async def test_facade_delete_room_memory_existing():
    repo = StateMemoryRepository(existing_doc())
    content_repo = StateContentRepository()

    assert await facade(memory_repo=repo, content_repo=content_repo).delete_room_memory(
        "r1"
    )
    assert repo.deleted == ["r1"]
    assert content_repo.deleted_rooms == ["r1"]


@pytest.mark.asyncio
async def test_facade_delete_room_memory_returns_false_on_content_cleanup_failure():
    content_repo = StateContentRepository()
    content_repo.raise_on_delete_room = True

    assert (
        await facade(
            memory_repo=StateMemoryRepository(None),
            content_repo=content_repo,
        ).delete_room_memory("r1")
        is False
    )


@pytest.mark.asyncio
async def test_facade_delete_room_memory_deletes_memory_before_content_cleanup_failure():
    repo = StateMemoryRepository(existing_doc())
    content_repo = StateContentRepository()
    content_repo.raise_on_delete_room = True

    assert (
        await facade(
            memory_repo=repo,
            content_repo=content_repo,
        ).delete_room_memory("r1")
        is False
    )
    assert repo.doc is None
    assert repo.deleted == ["r1"]


@pytest.mark.asyncio
async def test_facade_delete_room_memory_preserves_backing_when_memory_delete_fails():
    repo = StateMemoryRepository(existing_doc(), fail_delete=True)
    content_repo = StateContentRepository()
    await content_repo.upsert_full_content(
        conversation_id="r1",
        turn_id="t1",
        document_id="doc1",
        content="full content",
        content_type="text",
        content_hash="hash",
        stored_at=NOW,
    )

    assert (
        await facade(
            memory_repo=repo,
            content_repo=content_repo,
        ).delete_room_memory("r1")
        is False
    )
    assert repo.doc is not None
    assert content_repo.docs["doc1"]["content"] == "full content"


@pytest.mark.asyncio
async def test_facade_project_message():
    repo = StateMemoryRepository(existing_doc())
    service = facade(
        memory_repo=repo,
        history=FakeHistoryReader(
            [
                SimpleNamespace(
                    message_id="m2",
                    conversation_id="r1",
                    message_type="user",
                    content="new",
                )
            ]
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

    result = await facade(memory_repo=repo, content_repo=content_repo).run_compaction(
        "r1"
    )

    assert result.compacted_count == 1
    assert repo.compacted[0]["turn_id"] == "message:m1"
