from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from common.dto import MessageCommitted
from container import register_context_memory_event_handlers

NOW = datetime(2026, 6, 21, tzinfo=UTC)


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def register_internal_handler(self, event_type: str, handler) -> None:
        self.handlers.setdefault(event_type, []).append(handler)


class FakeContextMemoryFacade:
    def __init__(self, status: dict | None = None) -> None:
        self.status = status or {"projected": True, "reason": "projected"}
        self.projected: list[tuple[str, str]] = []
        self.compacted: list[str] = []

    async def project_message_for_event(
        self,
        room_id: str,
        message_id: str,
        **kwargs,
    ) -> dict:
        self.projected.append((room_id, message_id))
        return self.status

    async def run_compaction(self, room_id: str):
        self.compacted.append(room_id)


@pytest.mark.asyncio
async def test_register_context_memory_event_handlers_projects_and_compacts():
    publisher = RecordingEventPublisher()
    facade = FakeContextMemoryFacade()

    handler = register_context_memory_event_handlers(
        event_publisher=publisher,
        context_memory_facade=facade,
    )

    assert publisher.handlers.keys() == {"message_committed"}
    assert publisher.handlers["message_committed"] == [
        handler.handle_message_committed
    ]

    await publisher.handlers["message_committed"][0](
        MessageCommitted(
            timestamp=NOW,
            payload={},
            room_id="room-1",
            message_id="msg-1",
            message_type="user",
        )
    )

    assert facade.projected == [("room-1", "msg-1")]
    assert facade.compacted == ["room-1"]


def test_validate_runtime_bindings_checks_app_state_delivery_facade():
    source = Path("container.py").read_text()

    assert 'getattr(app.state, "delivery_facade", None)' in source
    assert 'getattr(sse_manager, "_facade", None)' not in source
    assert "sse_manager.delivery_facade" not in source
    assert "app.state.execution_deps.hitl_manager" not in source
