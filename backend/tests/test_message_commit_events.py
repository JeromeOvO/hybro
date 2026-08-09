from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from common.dto import MessageCommitted
from common.message_commit_events import publish_message_committed
from context_memory import projection
from context_memory.events import ContextMemoryEventHandler

NOW = datetime(2026, 6, 21, tzinfo=UTC)


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.internal_events = []
        self.wait_flags = []
        self.broadcast_flags = []

    async def publish(
        self,
        event,
        *,
        wait_for_handlers: bool = False,
        fanout: bool = True,
    ) -> None:
        self.internal_events.append(event)
        self.wait_flags.append(wait_for_handlers)
        self.broadcast_flags.append(fanout)


class SchedulingEventPublisher:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}
        self.tasks: list[asyncio.Task] = []

    def register_handler(self, event_type: str, handler) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    async def publish(
        self,
        event,
        *,
        wait_for_handlers: bool = False,
        fanout: bool = True,
    ) -> None:
        tasks = [
            asyncio.create_task(handler(event))
            for handler in self.handlers.get(event.event_type, [])
        ]
        self.tasks.extend(tasks)
        if wait_for_handlers and tasks:
            await asyncio.gather(*tasks)


class StateMemoryRepository:
    def __init__(self) -> None:
        self.doc: dict | None = None

    async def get_room_memory(self, room_id: str) -> dict | None:
        if self.doc and self.doc.get("room_id") == room_id:
            return self.doc
        return None

    async def ensure_room_memory(self, room_id: str, defaults: dict) -> dict:
        if self.doc is None:
            self.doc = defaults
        return self.doc

    async def push_and_trim_conversation_turn_if_absent(
        self,
        room_id: str,
        turn: dict,
        **kwargs,
    ):
        if self.doc is None:
            return False, False, False
        existing = {
            item["turn_id"] for item in self.doc.setdefault("conversation_history", [])
        }
        if turn["turn_id"] in existing:
            return False, True, True
        self.doc["conversation_history"].append(turn)
        self.doc.setdefault("memory_content", {}).setdefault(
            "conversation_history", []
        ).append(turn)
        return True, True, False


class FakeHistoryReader:
    def __init__(self, messages) -> None:
        self.messages = messages

    async def get_messages_by_ids(self, message_ids: list[str]):
        return [
            message for message in self.messages if message.message_id in message_ids
        ]


class MemoryProjector:
    def __init__(self, repository: StateMemoryRepository, history: FakeHistoryReader):
        self.repository = repository
        self.history = history
        self.compacted_rooms: list[str] = []

    async def project_message_for_event(
        self,
        room_id: str,
        message_id: str,
        **kwargs,
    ) -> dict:
        return await projection.project_message_from_history(
            room_id=room_id,
            message_id=message_id,
            repository=self.repository,
            room_history_reader=self.history,
            id_factory=lambda: "memory-1",
            now=lambda: NOW,
            **kwargs,
        )

    async def run_compaction(self, room_id: str):
        self.compacted_rooms.append(room_id)


@pytest.mark.asyncio
async def test_publish_message_committed_emits_user_event():
    publisher = RecordingEventPublisher()

    await publish_message_committed(
        publisher,
        room_id="room-1",
        message_id="user-msg-1",
        message_type="user",
        room_agent_set={"a1": "Canonical Agent"},
    )

    assert len(publisher.internal_events) == 1
    event = publisher.internal_events[0]
    assert isinstance(event, MessageCommitted)
    assert event.event_type == "message_committed"
    assert event.room_id == "room-1"
    assert event.message_id == "user-msg-1"
    assert event.message_type == "user"
    assert event.agent_id is None
    assert event.room_agent_set == {"a1": "Canonical Agent"}
    assert event.payload == {}
    assert event.timestamp.tzinfo is not None
    assert publisher.wait_flags == [False]
    assert publisher.broadcast_flags == [False]


@pytest.mark.asyncio
async def test_publish_message_committed_can_wait_for_local_handlers():
    publisher = RecordingEventPublisher()

    await publish_message_committed(
        publisher,
        room_id="room-1",
        message_id="user-msg-1",
        message_type="user",
        wait_for_handlers=True,
    )

    assert publisher.wait_flags == [True]
    assert publisher.broadcast_flags == [False]


@pytest.mark.asyncio
async def test_publish_message_committed_emits_agent_event_with_agent_id():
    publisher = RecordingEventPublisher()

    await publish_message_committed(
        publisher,
        room_id="room-1",
        message_id="agent-msg-1",
        message_type="agent",
        agent_id="agent-1",
        agent_name="Agent One",
        was_successful=True,
    )

    event = publisher.internal_events[0]
    assert isinstance(event, MessageCommitted)
    assert event.room_id == "room-1"
    assert event.message_id == "agent-msg-1"
    assert event.message_type == "agent"
    assert event.agent_id == "agent-1"
    assert event.agent_name == "Agent One"
    assert event.was_successful is True


@pytest.mark.asyncio
async def test_new_room_user_then_agent_events_project_both_turns():
    repository = StateMemoryRepository()
    history = FakeHistoryReader(
        [
            SimpleNamespace(
                room_id="room-1",
                message_id="user-msg-1",
                message_type="user",
                content={"message_text": "start"},
                sender_id="user-1",
                created_at=NOW,
            ),
            SimpleNamespace(
                room_id="room-1",
                message_id="agent-msg-1",
                message_type="agent",
                content={"message_text": "done"},
                agent_id="agent-1",
                created_at=NOW,
            ),
        ]
    )
    projector = MemoryProjector(repository, history)
    handler = ContextMemoryEventHandler(
        projector=projector,
        project_for_event=projector.project_message_for_event,
    )
    publisher = SchedulingEventPublisher()
    publisher.register_handler(
        "message_committed",
        handler.handle_message_committed,
    )

    await publish_message_committed(
        publisher,
        room_id="room-1",
        message_id="user-msg-1",
        message_type="user",
        wait_for_handlers=True,
    )
    await publish_message_committed(
        publisher,
        room_id="room-1",
        message_id="agent-msg-1",
        message_type="agent",
        agent_id="agent-1",
    )
    await asyncio.gather(*publisher.tasks)

    assert repository.doc is not None
    assert [turn["turn_id"] for turn in repository.doc["conversation_history"]] == [
        "message:user-msg-1",
        "message:agent-msg-1",
    ]
    assert projector.compacted_rooms == ["room-1", "room-1"]
