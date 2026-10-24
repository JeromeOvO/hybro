from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from context_memory import projection
from context_memory.translators import normalize_room_memory
from conversation.translators import message_info_from_doc

NOW = datetime(2026, 5, 13, tzinfo=UTC)


class StateMemoryRepository:
    def __init__(self, doc: dict | None = None, *, duplicate: bool = False):
        self.doc = doc
        self.duplicate = duplicate
        self.created: list[dict] = []
        self.pushed: list[dict] = []

    async def get_room_memory(self, conversation_id: str) -> dict | None:
        return (
            self.doc
            if self.doc and self.doc.get("room_id") == conversation_id
            else None
        )

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
        if self.duplicate:
            return False, True, True
        if not self.doc:
            return False, False, False
        self.pushed.append(turn)
        self.doc.setdefault("conversation_history", []).append(turn)
        return True, True, False


class FakeHistoryReader:
    def __init__(self, messages):
        self.messages = messages

    async def get_messages_by_ids(self, message_ids: list[str]):
        return [
            message for message in self.messages if message.message_id in message_ids
        ]


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
        projection.new_room_memory_doc(conversation_id="r1", memory_id="m1", now=NOW)
    )
    reader = FakeHistoryReader(
        [
            SimpleNamespace(
                message_id="msg-1",
                conversation_id="r1",
                message_type="user",
                content="Ship it",
                sender_id="u1",
                created_at=NOW,
            )
        ]
    )

    result = await projection.project_message_from_history(
        conversation_id="r1",
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
        conversation_id="r1",
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
        projection.new_room_memory_doc(conversation_id="r1", memory_id="m1", now=NOW),
        duplicate=True,
    )
    reader = FakeHistoryReader(
        [
            SimpleNamespace(
                message_id="msg-1",
                conversation_id="r1",
                message_type="user",
                content="hello",
            )
        ]
    )

    result = await projection.project_message_from_history(
        conversation_id="r1",
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
        conversation_id="r1",
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
async def test_project_message_from_history_agent_uses_event_metadata():
    repo = StateMemoryRepository(
        projection.new_room_memory_doc(conversation_id="r1", memory_id="m1", now=NOW)
    )
    response_text = "agent response " * 400
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
        conversation_id="r1",
        message_id="agent-msg-1",
        repository=repo,
        room_history_reader=reader,
        id_factory=id_factory(),
        now=now,
        agent_name="Agent One",
        was_successful=True,
    )

    assert result == {"projected": True, "reason": "projected"}
    turn = repo.pushed[0]
    assert turn["turn_id"] == "message:agent-msg-1"
    assert turn["agent_id"] == "agent-1"
    assert turn["agent_name"] == "Agent One"
    assert turn["was_successful"] is True
    assert "turn_notes" not in turn


@pytest.mark.asyncio
async def test_project_message_from_history_agent_falls_back_to_translated_metadata():
    repo = StateMemoryRepository(
        projection.new_room_memory_doc(conversation_id="r1", memory_id="m1", now=NOW)
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
        conversation_id="r1",
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
