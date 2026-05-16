from __future__ import annotations

from datetime import datetime, timezone

import pytest

from common.dto import MessageCommitted
from context_memory.events import ContextMemoryEventHandler


NOW = datetime(2026, 5, 13, tzinfo=timezone.utc)


class FakeProjector:
    def __init__(self):
        self.compacted_rooms = []

    async def run_compaction(self, room_id: str):
        self.compacted_rooms.append(room_id)


def event(message_id: str = "m1") -> MessageCommitted:
    return MessageCommitted(
        timestamp=NOW,
        payload={},
        room_id="r1",
        message_id=message_id,
        message_type="user",
    )


@pytest.mark.asyncio
async def test_handle_message_committed_projects_and_compacts():
    projector = FakeProjector()
    handler = ContextMemoryEventHandler(
        projector,
        lambda room_id, message_id: _status({"projected": True, "reason": "projected"}),
    )

    await handler.handle_message_committed(event())

    assert projector.compacted_rooms == ["r1"]


@pytest.mark.asyncio
async def test_handle_message_committed_duplicate_still_compacts():
    projector = FakeProjector()
    handler = ContextMemoryEventHandler(
        projector,
        lambda room_id, message_id: _status({"projected": False, "reason": "duplicate"}),
    )

    await handler.handle_message_committed(event())

    assert projector.compacted_rooms == ["r1"]


@pytest.mark.asyncio
async def test_handle_message_committed_missing_skips_compaction():
    projector = FakeProjector()
    handler = ContextMemoryEventHandler(
        projector,
        lambda room_id, message_id: _status({"projected": False, "reason": "missing_message"}),
    )

    await handler.handle_message_committed(event())

    assert projector.compacted_rooms == []


@pytest.mark.asyncio
async def test_handle_message_committed_exception_propagates():
    projector = FakeProjector()
    handler = ContextMemoryEventHandler(projector, _raise)

    with pytest.raises(RuntimeError, match="boom"):
        await handler.handle_message_committed(event())


async def _status(value: dict) -> dict:
    return value


async def _raise(room_id: str, message_id: str) -> dict:
    raise RuntimeError("boom")
