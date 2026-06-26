from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from delivery.facade import DeliveryFacade
from tests.delivery_adapter_fakes import (
    FakeDeliveryCompat,
    FakeEventPublisher,
    make_delivery_facade,
)


def _bind(
    compat: FakeDeliveryCompat | None = None,
    event_publisher: FakeEventPublisher | None = None,
) -> DeliveryFacade:
    return make_delivery_facade(
        compat=compat,
        event_publisher=event_publisher,
        instance_id="worker-bound",
    )


@pytest.mark.asyncio
async def test_send_processing_status_emits_typed_event_and_skips_recording(
    monkeypatch,
):
    import execution.run_command_handler as handler_mod

    record = AsyncMock()
    monkeypatch.setattr(
        handler_mod.run_command_handler,
        "record_processing_status",
        record,
    )
    compat = FakeDeliveryCompat()
    fake_publisher = FakeEventPublisher()
    facade = _bind(compat=compat, event_publisher=fake_publisher)

    await facade.send_processing_status(
        "room-1",
        "rate_limited",
        "msg-1",
        details={"message": "typed detail"},
        client_request_id="cr-1",
        agents=[{"agent_id": "a1"}],
    )

    record.assert_not_awaited()
    assert [frame_type for frame_type, _data in compat.frames] == [
        "processing_status"
    ]
    assert len(fake_publisher.events) == 1
    event = fake_publisher.events[0]
    assert event.event_type == "processing_status"
    assert event.room_id == "room-1"
    assert event.message_id == "msg-1"
    assert event.status == "rate_limited"
    assert event.details == {"message": "typed detail"}
    assert event.client_request_id == "cr-1"
    assert event.agents == [{"agent_id": "a1"}]


@pytest.mark.asyncio
async def test_send_methods_emit_typed_events():
    compat = FakeDeliveryCompat()
    fake_publisher = FakeEventPublisher()
    facade = _bind(compat=compat, event_publisher=fake_publisher)

    await facade.send_agent_response(
        "room-1",
        "msg-1",
        "agent-1",
        "hello",
        related_message_id="root",
        parts=[{"type": "text"}],
        client_request_id="cr-1",
    )
    await facade.send_artifact_update(
        "room-1",
        "msg-2",
        "agent-1",
        {"kind": "file"},
        append=True,
        last_chunk=True,
        client_request_id="cr-2",
    )
    await facade.send_task_submitted(
        "room-1",
        "msg-3",
        "task-1",
        "Agent",
        agent_id="agent-1",
        status="working",
        related_message_id="root",
        created_at="created",
        step_number=1,
        total_steps=2,
        task_content="do work",
        client_request_id="cr-3",
    )
    await facade.send_task_update(
        "room-1",
        "msg-4",
        "input_required",
        content="content",
        error="error",
        requires_input=True,
        requires_auth=True,
        status_message="waiting",
        agent_name="Agent",
        agent_id="agent-1",
        related_message_id="root",
        created_at="created",
        step_number=2,
        total_steps=2,
        task_content="do work",
        parts=[{"type": "text"}],
        client_request_id="cr-4",
    )
    await facade.send_error("room-1", "boom", message_id="msg-5")
    await facade.send_rate_limit_error(
        "room-1",
        "msg-6",
        "agent-1",
        "slow down",
        retry_after_seconds=5,
        user_requests_used=1,
        user_requests_limit=2,
        system_requests_used=3,
        system_requests_limit=4,
    )
    assert [frame_type for frame_type, _data in compat.frames] == [
        "agent_response",
        "artifact_update",
        "task_submitted",
        "task_update",
        "error",
        "error",
    ]
    events = fake_publisher.events
    assert [event.event_type for event in events] == [
        "agent_message_final",
        "artifact_update",
        "task_submitted",
        "task_update",
        "error",
        "error",
    ]
    assert events[0].content == {
        "content": "hello",
        "related_message_id": "root",
        "client_request_id": "cr-1",
        "parts": [{"type": "text"}],
    }
    assert events[1].client_request_id == "cr-2"
    assert events[2].created_at == "created"
    assert events[3].status == "input_required"
    assert events[3].created_at == "created"
    assert events[4].error == "boom"
    assert events[5].error_type == "rate_limit_exceeded"
    assert events[5].retry_after_seconds == 5


@pytest.mark.asyncio
async def test_cancellation_helpers_delegate_to_transport():
    facade = _bind()

    token = facade.create_token("msg-1")
    facade.cancel_message("msg-1")
    assert facade.is_cancelled("msg-1") is True
    assert token.is_cancelled is True
    assert await facade.check_cancelled("msg-1") is True
    facade.clear_cancellation("msg-1")
    assert facade.is_cancelled("msg-1") is False


@pytest.mark.asyncio
async def test_lifecycle_start_stop_uses_delivery_surfaces():
    compat = FakeDeliveryCompat()
    fake_publisher = FakeEventPublisher()
    facade = _bind(compat=compat, event_publisher=fake_publisher)

    await facade.start()
    await facade.stop()

    assert compat.lifecycle_calls == [
        ("start_cancellation_watcher", None),
        ("start", None),
        ("refresh_health", None),
        ("close_all_connections", None),
        ("stop", None),
        ("stop_cancellation_watcher", None),
    ]
    assert fake_publisher.lifecycle_calls == [("start", None), ("stop", None)]
