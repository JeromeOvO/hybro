from __future__ import annotations

import inspect
from datetime import UTC, datetime

from room.facade import RoomFacade


def test_room_facade_exposes_hub_publish_support_protocol_methods() -> None:
    assert inspect.iscoroutinefunction(RoomFacade.is_message_cancelled)
    assert inspect.iscoroutinefunction(RoomFacade.get_turn_completion_kind)


async def test_room_facade_cancellation_reader_uses_repository_cancel_store() -> None:
    class Messages:
        async def is_message_cancelled(self, message_id: str) -> bool:
            assert message_id == "msg-1"
            return True

        async def get_by_id(self, message_id: str):
            raise AssertionError("document fallback should not run")

    facade = RoomFacade(
        repository=None,
        message_repository=Messages(),
        agent_registry=None,
        membership_source=None,
        id_factory=lambda: "id",
        now=lambda: datetime.now(UTC),
    )

    assert await facade.is_message_cancelled("msg-1") is True
