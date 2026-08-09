from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from common.utils.context_utils import LLM_TURN_NOTES_THRESHOLD
from context_memory import projection
from context_memory.config import ContextMemoryLLMConfig
from context_memory.translators import normalize_room_memory
from room.translators import message_info_from_doc

NOW = datetime(2026, 5, 13, tzinfo=UTC)


class StateMemoryRepository:
    def __init__(self, doc: dict | None = None, *, duplicate: bool = False):
        self.doc = doc
        self.duplicate = duplicate
        self.created: list[dict] = []
        self.pushed: list[dict] = []
        self.updated_notes: list[tuple[str, str, dict]] = []

    async def get_room_memory(self, room_id: str) -> dict | None:
        return self.doc if self.doc and self.doc.get("room_id") == room_id else None

    async def create_room_memory(self, memory: dict) -> str:
        self.doc = memory
        self.created.append(memory)
        return memory["memory_id"]

    async def ensure_room_memory(self, room_id: str, defaults: dict) -> dict:
        if self.doc is None:
            self.doc = defaults
        return self.doc

    async def push_and_trim_conversation_turn(self, room_id: str, turn: dict, **kwargs):
        if not self.doc:
            return False, False
        self.pushed.append(turn)
        self.doc.setdefault("conversation_history", []).append(turn)
        return True, True

    async def push_and_trim_conversation_turn_if_absent(
        self, room_id: str, turn: dict, **kwargs
    ):
        if self.duplicate:
            return False, True, True
        if not self.doc:
            return False, False, False
        self.pushed.append(turn)
        self.doc.setdefault("conversation_history", []).append(turn)
        return True, True, False

    async def update_turn_notes(
        self, room_id: str, turn_id: str, turn_notes: dict
    ) -> bool:
        self.updated_notes.append((room_id, turn_id, turn_notes))
        return True


class FakeHistoryReader:
    def __init__(self, messages):
        self.messages = messages

    async def get_messages_by_ids(self, message_ids: list[str]):
        return [
            message for message in self.messages if message.message_id in message_ids
        ]


class FakeLLM:
    async def generate_structured(self, messages, schema, model=None):
        return SimpleNamespace(
            data={
                "keywords": ["alpha"],
                "entities": ["Hybro"],
                "tags": ["memory"],
                "one_liner": "Enriched turn notes",
            }
        )


class RaisingLLM:
    async def generate_structured(self, messages, schema, model=None):
        raise RuntimeError("turn notes llm failed")


class FailingNotesMemoryRepository(StateMemoryRepository):
    async def update_turn_notes(
        self, room_id: str, turn_id: str, turn_notes: dict
    ) -> bool:
        raise RuntimeError("notes write failed")


def id_factory(values=("id-1", "id-2", "id-3")):
    ids = iter(values)
    return lambda: next(ids)


def now():
    return NOW


def test_build_turn_content_plain_text():
    assert projection.build_turn_content("hello") == "hello"


def test_build_turn_content_with_attachments():
    content = projection.build_turn_content(
        "hello",
        [
            {
                "file_name": "spec.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 2048,
                "url": "https://example.test/spec.pdf",
            }
        ],
    )

    assert content == "hello\n[Attachments: spec.pdf (application/pdf, 2KB)]"


def test_user_turn_creates_deterministic_turn_id():
    turn = projection.user_turn(
        message_id="m1",
        content="hello",
        user_id="u1",
        timestamp=NOW,
    )

    assert turn["turn_id"] == "message:m1"
    assert turn["role"] == "user"


def test_agent_turn_uses_provided_turn_id():
    turn = projection.agent_turn(
        content="done",
        agent_id="a1",
        agent_name="Agent One",
        timestamp=NOW,
        turn_id="message:m2",
    )

    assert turn["turn_id"] == "message:m2"
    assert turn["agent_name"] == "Agent One"


@pytest.mark.asyncio
async def test_project_message_from_history_user_message():
    repo = StateMemoryRepository(
        projection.new_room_memory_doc(room_id="r1", memory_id="m1", now=NOW)
    )
    reader = FakeHistoryReader(
        [
            SimpleNamespace(
                message_id="msg-1",
                room_id="r1",
                message_type="user",
                content="Ship it",
                sender_id="u1",
                created_at=NOW,
            )
        ]
    )

    result = await projection.project_message_from_history(
        room_id="r1",
        message_id="msg-1",
        repository=repo,
        room_history_reader=reader,
        id_factory=id_factory(),
        now=now,
    )

    assert result == {"projected": True, "reason": "projected"}
    assert repo.pushed[0]["turn_id"] == "message:msg-1"


@pytest.mark.asyncio
async def test_project_message_from_history_missing_message():
    result = await projection.project_message_from_history(
        room_id="r1",
        message_id="missing",
        repository=StateMemoryRepository(),
        room_history_reader=FakeHistoryReader([]),
        id_factory=id_factory(),
        now=now,
    )

    assert result == {"projected": False, "reason": "missing_message"}


@pytest.mark.asyncio
async def test_project_message_from_history_duplicate():
    repo = StateMemoryRepository(
        projection.new_room_memory_doc(room_id="r1", memory_id="m1", now=NOW),
        duplicate=True,
    )
    reader = FakeHistoryReader(
        [
            SimpleNamespace(
                message_id="msg-1", room_id="r1", message_type="user", content="hello"
            )
        ]
    )

    result = await projection.project_message_from_history(
        room_id="r1",
        message_id="msg-1",
        repository=repo,
        room_history_reader=reader,
        id_factory=id_factory(),
        now=now,
    )

    assert result == {"projected": False, "reason": "duplicate"}


@pytest.mark.asyncio
async def test_project_message_from_history_user_cleans_mentions_before_attachments():
    repo = StateMemoryRepository()
    reader = FakeHistoryReader(
        [
            message_info_from_doc(
                {
                    "message_id": "user-msg-1",
                    "room_id": "r1",
                    "message_type": "user",
                    "user_id": "user-1",
                    "message_content": {
                        "message_text": "Please ask <@a1|Stale Name> for help",
                        "attachments": [
                            {
                                "file_name": "spec.pdf",
                                "mime_type": "application/pdf",
                                "size_bytes": 2048,
                            }
                        ],
                    },
                    "message_created_at": NOW,
                }
            )
        ]
    )

    result = await projection.project_message_from_history(
        room_id="r1",
        message_id="user-msg-1",
        repository=repo,
        room_history_reader=reader,
        id_factory=id_factory(),
        now=now,
        room_agent_set={"a1": "Canonical Agent"},
    )

    assert result == {"projected": True, "reason": "projected"}
    turn = repo.pushed[0]
    assert turn["turn_id"] == "message:user-msg-1"
    assert (
        turn["content"] == "Please ask @Canonical Agent for help\n"
        "[Attachments: spec.pdf (application/pdf, 2KB)]"
    )


@pytest.mark.asyncio
async def test_project_message_from_history_agent_uses_event_metadata_and_enriches_notes():
    repo = StateMemoryRepository(
        projection.new_room_memory_doc(room_id="r1", memory_id="m1", now=NOW)
    )
    background_tasks = []
    response_text = "agent response " * (LLM_TURN_NOTES_THRESHOLD * 4)
    reader = FakeHistoryReader(
        [
            message_info_from_doc(
                {
                    "message_id": "agent-msg-1",
                    "room_id": "r1",
                    "message_type": "agent",
                    "agent_id": "agent-1",
                    "message_content": {"message_text": response_text},
                    "message_created_at": NOW,
                }
            )
        ]
    )

    result = await projection.project_message_from_history(
        room_id="r1",
        message_id="agent-msg-1",
        repository=repo,
        room_history_reader=reader,
        id_factory=id_factory(),
        now=now,
        agent_name="Agent One",
        was_successful=True,
        llm_provider=FakeLLM(),
        llm_config=ContextMemoryLLMConfig(),
        background_task_runner=background_tasks.append,
    )

    assert result == {"projected": True, "reason": "projected"}
    turn = repo.pushed[0]
    assert turn["turn_id"] == "message:agent-msg-1"
    assert turn["agent_id"] == "agent-1"
    assert turn["agent_name"] == "Agent One"
    assert turn["was_successful"] is True
    assert len(background_tasks) == 1
    await background_tasks[0]
    assert repo.updated_notes == [
        (
            "r1",
            "message:agent-msg-1",
            {
                "keywords": ["alpha"],
                "entities": ["Hybro"],
                "tags": ["memory"],
                "one_liner": "Enriched turn notes",
            },
        )
    ]


@pytest.mark.asyncio
async def test_project_message_from_history_agent_falls_back_to_translated_metadata():
    repo = StateMemoryRepository(
        projection.new_room_memory_doc(room_id="r1", memory_id="m1", now=NOW)
    )
    reader = FakeHistoryReader(
        [
            message_info_from_doc(
                {
                    "message_id": "agent-msg-1",
                    "room_id": "r1",
                    "message_type": "agent",
                    "agent_id": "agent-1",
                    "agent_name": "Agent One",
                    "was_successful": True,
                    "message_content": {"message_text": "agent response"},
                    "message_created_at": NOW,
                }
            )
        ]
    )

    result = await projection.project_message_from_history(
        room_id="r1",
        message_id="agent-msg-1",
        repository=repo,
        room_history_reader=reader,
        id_factory=id_factory(),
        now=now,
    )

    assert result == {"projected": True, "reason": "projected"}
    turn = repo.pushed[0]
    assert turn["agent_name"] == "Agent One"
    assert turn["was_successful"] is True


@pytest.mark.asyncio
async def test_initialize_or_update_room_memory_creates_new():
    repo = StateMemoryRepository()

    doc = await projection.initialize_or_update_room_memory(
        repository=repo,
        room_id="r1",
        memory_content=None,
        room_agent_set=None,
        user_id="u1",
        attachments=None,
        id_factory=id_factory(),
        now=now,
    )

    assert doc["room_id"] == "r1"
    assert repo.created[0]["memory_id"] == "id-1"


@pytest.mark.asyncio
async def test_initialize_or_update_room_memory_adds_turn():
    repo = StateMemoryRepository()

    doc = await projection.initialize_or_update_room_memory(
        repository=repo,
        room_id="r1",
        memory_content="Hello @agent",
        room_agent_set={},
        user_id="u1",
        attachments=[{"name": "note.txt"}],
        message_id="msg-1",
        id_factory=id_factory(("m1", "message-id", "turn-id")),
        now=now,
    )

    assert doc["conversation_history"][0]["turn_id"] == "message:msg-1"
    assert "[Attachments: note.txt" in doc["conversation_history"][0]["content"]


@pytest.mark.asyncio
async def test_initialize_or_update_room_memory_consumes_one_id_for_legacy_turn():
    repo = StateMemoryRepository()

    doc = await projection.initialize_or_update_room_memory(
        repository=repo,
        room_id="r1",
        memory_content="Hello",
        room_agent_set={},
        user_id="u1",
        attachments=None,
        id_factory=id_factory(("memory-id", "turn-id")),
        now=now,
    )

    assert doc["conversation_history"][0]["turn_id"] == "turn-id"


@pytest.mark.asyncio
async def test_initialize_or_update_room_memory_skips_duplicate_message_turn():
    repo = StateMemoryRepository(
        projection.new_room_memory_doc(room_id="r1", memory_id="m1", now=NOW),
        duplicate=True,
    )

    doc = await projection.initialize_or_update_room_memory(
        repository=repo,
        room_id="r1",
        memory_content="Hello",
        room_agent_set={},
        user_id="u1",
        attachments=None,
        message_id="msg-1",
        id_factory=id_factory(),
        now=now,
    )

    assert doc["conversation_history"] == []
    assert doc["_context_memory_duplicate_turn"] is True


@pytest.mark.asyncio
async def test_add_agent_response_triggers_turn_notes_enrichment(monkeypatch):
    monkeypatch.setattr(projection, "LLM_TURN_NOTES_THRESHOLD", 1)
    repo = StateMemoryRepository(
        projection.new_room_memory_doc(room_id="r1", memory_id="m1", now=NOW)
    )
    scheduled = []

    await projection.add_agent_response_to_memory(
        repository=repo,
        room_id="r1",
        agent_id="a1",
        agent_name="Agent",
        response_text="long enough for enrichment",
        was_successful=True,
        message_id="agent-msg-1",
        id_factory=id_factory(),
        now=now,
        llm_provider=FakeLLM(),
        llm_config=ContextMemoryLLMConfig(),
        background_task_runner=scheduled.append,
    )
    await scheduled[0]

    assert repo.updated_notes == [
        (
            "r1",
            "message:agent-msg-1",
            {
                "keywords": ["alpha"],
                "entities": ["Hybro"],
                "tags": ["memory"],
                "one_liner": "Enriched turn notes",
            },
        )
    ]


@pytest.mark.asyncio
async def test_add_agent_response_skips_duplicate_message_turn():
    repo = StateMemoryRepository(
        projection.new_room_memory_doc(room_id="r1", memory_id="m1", now=NOW),
        duplicate=True,
    )

    modified, matched = await projection.add_agent_response_to_memory(
        repository=repo,
        room_id="r1",
        agent_id="a1",
        agent_name="Agent",
        response_text="hello",
        was_successful=True,
        message_id="agent-msg-1",
        id_factory=id_factory(),
        now=now,
        llm_provider=FakeLLM(),
        llm_config=ContextMemoryLLMConfig(),
        background_task_runner=lambda coro: None,
    )

    assert (modified, matched) == (False, True)
    assert repo.pushed == []


@pytest.mark.asyncio
async def test_enrich_turn_notes_swallows_persistence_failures(monkeypatch):
    monkeypatch.setattr(
        projection,
        "extract_turn_notes_llm",
        AsyncMock(return_value={"one_liner": "new notes"}),
    )

    await projection.enrich_turn_notes(
        repository=FailingNotesMemoryRepository(),
        llm_provider=FakeLLM(),
        llm_config=ContextMemoryLLMConfig(),
        room_id="r1",
        turn_id="t1",
        heuristic_notes={"one_liner": "old notes"},
        content="long enough content for enrichment",
    )


@pytest.mark.asyncio
async def test_extract_turn_notes_llm_logs_fallback_failure(caplog):
    caplog.set_level("DEBUG")

    notes = await projection.extract_turn_notes_llm(
        "long enough content for heuristic fallback",
        llm_provider=RaisingLLM(),
        llm_config=ContextMemoryLLMConfig(),
    )

    assert notes["one_liner"] == "long enough content for heuristic fallback"
    assert "LLM turn note extraction failed; using heuristic notes" in caplog.text


@pytest.mark.asyncio
async def test_add_synthesis_to_history():
    repo = StateMemoryRepository(
        projection.new_room_memory_doc(room_id="r1", memory_id="m1", now=NOW)
    )

    turn_id = await projection.add_synthesis_to_history(
        repository=repo,
        room_id="r1",
        synthesis_text="Summary",
        trajectory=None,
        id_factory=id_factory(),
        now=now,
        llm_provider=FakeLLM(),
        llm_config=ContextMemoryLLMConfig(),
        background_task_runner=lambda coro: None,
    )

    assert turn_id == "id-1"
    assert repo.pushed[0]["role"] == "supervisor"


def test_enrich_synthesis_with_trajectory():
    trajectory = SimpleNamespace(
        entries=[
            SimpleNamespace(
                results=[
                    SimpleNamespace(
                        success=True, agent_name="Builder", task="implement tests"
                    ),
                    SimpleNamespace(success=False, agent_name="Skip", task="ignore"),
                ]
            )
        ]
    )

    enriched = projection.enrich_synthesis_with_trajectory("Summary", trajectory)

    assert "Builder: implement tests" in enriched
    assert "Skip" not in enriched


def test_extract_message_text_string():
    assert projection.extract_message_text("hello") == "hello"


def test_extract_message_text_dict():
    assert projection.extract_message_text({"message_text": "hello"}) == "hello"


def test_extract_message_text_empty_values():
    assert projection.extract_message_text(None) == ""
    assert projection.extract_message_text({}) == ""


def test_normalize_room_memory_treats_empty_direct_history_as_canonical():
    state = normalize_room_memory(
        {
            "room_id": "r1",
            "memory_id": "m1",
            "conversation_history": [],
            "memory_content": {
                "conversation_history": [
                    {"turn_id": "legacy", "role": "user", "content": "legacy"}
                ]
            },
        }
    )

    assert state.conversation_history == []
