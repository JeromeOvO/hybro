from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models.room import MessageContent, Room, RoomUserMessage
from room.compat.runtime import RoomServices


def _room() -> Room:
    return Room(
        room_id="room-1",
        room_name="Room",
        room_owner_id="owner-1",
        room_owner_name="Owner",
        room_agent_set={
            "agent-1": "Agent One",
            "agent-2": "Agent Two",
        },
    )


def _message() -> RoomUserMessage:
    return RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        client_request_id="request-1",
        message_content=MessageContent(
            message_text="<@agent-1|Agent One> <@agent-2|Agent Two> please help"
        ),
    )


def _service(*, add_side_effect) -> RoomServices:
    service = object.__new__(RoomServices)
    add_room_agent_message = AsyncMock()
    if isinstance(add_side_effect, list):
        add_room_agent_message.side_effect = add_side_effect
    else:
        add_room_agent_message.return_value = add_side_effect
    service._store = SimpleNamespace(
        add_room_agent_message=add_room_agent_message,
    )
    return service


def _mentions(service: RoomServices, message: RoomUserMessage) -> list[dict]:
    return service.parse_agent_mentions(
        message.message_content.message_text,
        _room().room_agent_set,
    )


@pytest.mark.asyncio
async def test_mention_fanout_does_not_report_ready_when_all_writes_return_false():
    service = _service(add_side_effect=False)
    message = _message()

    response = await service.parse_user_message_with_mentions(
        _room(),
        message,
        _mentions(service, message),
    )

    assert response.success is False
    assert response.status_code == 500
    assert response.preflight_outcome == "failed"
    assert response.preflight_details == (
        "Failed to create agent messages for mentioned agents"
    )
    assert response.dispatch_root_message_id is None
    assert service._store.add_room_agent_message.await_count == 2


@pytest.mark.asyncio
async def test_mention_fanout_does_not_report_ready_when_context_creation_raises(
    caplog,
):
    service = _service(add_side_effect=True)
    service.create_task_for_agents_group = AsyncMock(
        side_effect=RuntimeError("task generation failed")
    )
    message = _message()

    response = await service.parse_user_message_with_mentions(
        _room(),
        message,
        _mentions(service, message),
    )

    assert response.success is False
    assert response.status_code == 500
    assert response.preflight_outcome == "failed"
    service._store.add_room_agent_message.assert_not_awaited()
    assert "Mention fan-out context failed" in caplog.text
    assert message.message_content.message_text not in caplog.text


@pytest.mark.asyncio
async def test_mention_fanout_keeps_ready_when_at_least_one_write_succeeds(caplog):
    service = _service(add_side_effect=[True, False])
    message = _message()

    response = await service.parse_user_message_with_mentions(
        _room(),
        message,
        _mentions(service, message),
    )

    assert response.success is True
    assert response.status_code == 200
    assert response.preflight_outcome == "ready"
    assert response.dispatch_root_message_id == "message-1"
    assert service._store.add_room_agent_message.await_count == 2
    assert "Mention fan-out partially persisted" in caplog.text


@pytest.mark.asyncio
async def test_mention_fanout_full_success_remains_ready(caplog):
    service = _service(add_side_effect=True)
    message = _message()

    response = await service.parse_user_message_with_mentions(
        _room(),
        message,
        _mentions(service, message),
    )

    assert response.success is True
    assert response.status_code == 200
    assert response.preflight_outcome == "ready"
    assert response.dispatch_root_message_id == "message-1"
    assert service._store.add_room_agent_message.await_count == 2
    assert "Mention fan-out partially persisted" not in caplog.text
