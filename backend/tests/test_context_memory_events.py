from __future__ import annotations

from datetime import UTC, datetime

import pytest

from common.dto import MessageCommitted
from context_memory.events import ContextMemoryEventHandler

NOW = datetime(2026, 5, 13, tzinfo=UTC)


class FakeProjection:
    def __init__(self, status: dict | None = None, failure: Exception | None = None):
        self.status = status or {"projected": True, "reason": "projected"}
        self.failure = failure
        self.calls = []
        self.compacted_rooms = []

    async def project_message_for_event(self, room_id: str, message_id: str, **kwargs):
        if self.failure is not None:
            raise self.failure
        self.calls.append((room_id, message_id, kwargs))
        return self.status

    async def run_compaction(self, room_id: str):
        self.compacted_rooms.append(room_id)


def event(
    message_id: str = "m1",
    *,
    message_type: str = "user",
    agent_id: str | None = None,
    room_agent_set: dict[str, str] | None = None,
    agent_name: str | None = None,
    was_successful: bool | None = None,
) -> MessageCommitted:
    return MessageCommitted(
        timestamp=NOW,
        payload={},
        room_id="r1",
        message_id=message_id,
        message_type=message_type,
        agent_id=agent_id,
        room_agent_set=room_agent_set,
        agent_name=agent_name,
        was_successful=was_successful,
    )


@pytest.mark.asyncio
async def test_handle_message_committed_projects_and_compacts():
    projection = FakeProjection()
    handler = ContextMemoryEventHandler(projection)

    await handler.handle_message_committed(event())

    assert projection.compacted_rooms == ["r1"]


@pytest.mark.asyncio
async def test_handle_message_committed_duplicate_skips_compaction():
    projection = FakeProjection({"projected": False, "reason": "duplicate"})
    handler = ContextMemoryEventHandler(projection)

    await handler.handle_message_committed(event())

    assert projection.compacted_rooms == []


@pytest.mark.asyncio
async def test_handle_message_committed_missing_skips_compaction():
    projection = FakeProjection({"projected": False, "reason": "missing_message"})
    handler = ContextMemoryEventHandler(projection)

    await handler.handle_message_committed(event())

    assert projection.compacted_rooms == []


@pytest.mark.asyncio
async def test_handle_message_committed_exception_propagates():
    projection = FakeProjection(failure=RuntimeError("boom"))
    handler = ContextMemoryEventHandler(projection)

    with pytest.raises(RuntimeError, match="boom"):
        await handler.handle_message_committed(event())


@pytest.mark.asyncio
async def test_handle_message_committed_passes_agent_metadata_to_projection():
    projection = FakeProjection()
    handler = ContextMemoryEventHandler(projection)

    await handler.handle_message_committed(
        event(
            "agent-msg-1",
            message_type="agent",
            agent_id="agent-1",
            agent_name="Agent One",
            was_successful=True,
        )
    )

    assert projection.calls == [
        (
            "r1",
            "agent-msg-1",
            {
                "room_agent_set": None,
                "agent_name": "Agent One",
                "was_successful": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_handle_message_committed_passes_room_agent_set_to_projection():
    projection = FakeProjection()
    handler = ContextMemoryEventHandler(projection)

    await handler.handle_message_committed(
        event("user-msg-1", room_agent_set={"a1": "Canonical Agent"})
    )

    assert projection.calls == [
        (
            "r1",
            "user-msg-1",
            {
                "room_agent_set": {"a1": "Canonical Agent"},
                "agent_name": None,
                "was_successful": None,
            },
        )
    ]
