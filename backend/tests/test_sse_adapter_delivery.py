from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app_shell.delivery_runtime import SSEManager
from tests.delivery_adapter_fakes import (
    FakeDeliveryCompat,
    FakeDeliveryFacade,
    FakeEventPublisher,
)

NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
FAIL_FAST = re.escape("SSEManager.bind_facade() not called - startup incomplete")


def _bind(
    manager: SSEManager,
    compat: FakeDeliveryCompat | None = None,
    event_publisher: FakeEventPublisher | None = None,
) -> FakeDeliveryCompat:
    compat = compat or FakeDeliveryCompat()
    manager.bind_facade(
        FakeDeliveryFacade(
            compat=compat,
            instance_id="worker-bound",
            event_publisher=event_publisher,
        )
    )
    return compat


async def _queued_frame(connection) -> dict:
    return json.loads(await connection.queue.get())


def test_public_methods_fail_fast_before_bind():
    manager = SSEManager()

    with pytest.raises(RuntimeError, match=FAIL_FAST):
        manager.set_draining(True)
    with pytest.raises(RuntimeError, match=FAIL_FAST):
        manager.get_room_status("room-1")
    with pytest.raises(RuntimeError, match=FAIL_FAST):
        manager.cancel_message("msg-1")
    with pytest.raises(RuntimeError, match=FAIL_FAST):
        manager.create_token("msg-1")


@pytest.mark.asyncio
async def test_async_public_methods_fail_fast_before_bind():
    manager = SSEManager()

    with pytest.raises(RuntimeError, match=FAIL_FAST):
        await manager.add_connection("room-1")
    with pytest.raises(RuntimeError, match=FAIL_FAST):
        await manager.start_event_broker(None)
    with pytest.raises(RuntimeError, match=FAIL_FAST):
        await manager.start_redis_service(None)
    with pytest.raises(RuntimeError, match=FAIL_FAST):
        await manager.start_change_stream_watcher(None)


@pytest.mark.asyncio
async def test_bind_unbind_and_rebind_delegates_to_current_facade():
    manager = SSEManager()
    first = _bind(manager)
    first_conn = await manager.add_connection("room-1")
    assert first_conn.connection_id in first.room_connections["room-1"]

    manager.unbind_facade()
    with pytest.raises(RuntimeError, match=FAIL_FAST):
        await manager.add_connection("room-1")

    second = _bind(manager)
    second_conn = await manager.add_connection("room-2")
    assert second_conn.connection_id in second.room_connections["room-2"]
    assert "room-2" not in first.room_connections


@pytest.mark.asyncio
async def test_send_processing_status_emits_typed_event_and_skips_recording(
    monkeypatch,
):
    monkeypatch.setattr("app_shell.delivery_runtime.utcnow", lambda: NOW)
    import execution.run_command_handler as handler_mod

    record = AsyncMock()
    monkeypatch.setattr(
        handler_mod.run_command_handler,
        "record_processing_status",
        record,
    )
    manager = SSEManager()
    fake_publisher = FakeEventPublisher()
    compat = _bind(manager, event_publisher=fake_publisher)

    await manager.send_processing_status(
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
async def test_send_methods_emit_typed_events(monkeypatch):
    monkeypatch.setattr("app_shell.delivery_runtime.utcnow", lambda: NOW)
    manager = SSEManager()
    fake_publisher = FakeEventPublisher()
    compat = _bind(manager, event_publisher=fake_publisher)

    await manager.send_agent_response(
        "room-1",
        "msg-1",
        "agent-1",
        "hello",
        related_message_id="root",
        parts=[{"type": "text"}],
        client_request_id="cr-1",
    )
    await manager.send_artifact_update(
        "room-1",
        "msg-2",
        "agent-1",
        {"kind": "file"},
        append=True,
        last_chunk=True,
        client_request_id="cr-2",
    )
    await manager.send_task_submitted(
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
    await manager.send_task_update(
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
    await manager.send_error("room-1", "boom", message_id="msg-5")
    await manager.send_rate_limit_error(
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
async def test_cancellation_and_lifecycle_methods_delegate():
    manager = SSEManager()
    compat = _bind(manager)

    token = manager.create_token("msg-1")
    manager.cancel_message("msg-1")
    assert manager.is_cancelled("msg-1") is True
    assert token.is_cancelled is True
    assert await manager.check_cancelled("msg-1") is True
    manager.clear_cancellation("msg-1")
    assert manager.is_cancelled("msg-1") is False

    await manager.start_event_broker("broker")
    await manager.start_redis_service("redis")
    await manager.start_change_stream_watcher("collection")
    await manager.stop_change_stream_watcher()
    await manager.stop_redis_service()
    await manager.stop_event_broker()

    assert compat.lifecycle_calls == [
        ("start_event_broker", "broker"),
        ("start_redis_service", "redis"),
        ("start_change_stream_watcher", None),
        ("stop_change_stream_watcher", None),
        ("stop_redis_service", None),
        ("stop_event_broker", None),
    ]
