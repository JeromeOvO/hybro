from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from delivery.config import DeliveryConfig
from delivery.facade import DeliveryFacade
from tests.fakes.delivery import (
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
async def test_send_methods_emit_typed_events():
    compat = FakeDeliveryCompat()
    fake_publisher = FakeEventPublisher()
    facade = _bind(compat=compat, event_publisher=fake_publisher)

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
    assert [frame_type for frame_type, _data in compat.frames] == [
        "task_submitted",
        "task_update",
    ]
    events = fake_publisher.events
    assert [event.event_type for event in events] == [
        "task_submitted",
        "task_update",
    ]
    assert events[0].created_at == "created"
    assert events[1].status == "input_required"
    assert events[1].created_at == "created"


@pytest.mark.core
@pytest.mark.asyncio
async def test_terminal_delivery_log_is_emitted_once_per_message(caplog):
    facade = _bind()
    caplog.set_level(logging.INFO, logger="delivery.facade")

    await facade.send_task_submitted(
        "room-1",
        "msg-1",
        "task-1",
        "Agent",
    )
    await facade.send_task_update("room-1", "msg-1", "completed")
    await facade.send_task_update("room-1", "msg-1", "completed")

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "delivery_completed"
    ]
    assert len(records) == 1
    assert records[0].terminal_kind == "task_update"


@pytest.mark.asyncio
async def test_terminal_delivery_log_reports_failed_handoff(caplog):
    publisher = FakeEventPublisher()
    publisher.emit = AsyncMock(return_value=False)
    facade = _bind(event_publisher=publisher)
    caplog.set_level(logging.INFO, logger="delivery.facade")

    await facade.send_task_update("room-1", "msg-failed", "completed")

    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "delivery_completed"
    )
    assert record.outcome == "delivery_failed"
    assert record.terminal_kind == "task_update"


@pytest.mark.asyncio
async def test_successful_retry_after_failed_handoff_logs_success(caplog):
    publisher = FakeEventPublisher()
    publisher.emit = AsyncMock(side_effect=[False, True])
    facade = _bind(event_publisher=publisher)
    caplog.set_level(logging.INFO, logger="delivery.facade")

    await facade.send_task_update("room-1", "msg-retry", "completed")
    await facade.send_task_update("room-1", "msg-retry", "completed")

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "delivery_completed"
    ]
    assert [record.outcome for record in records] == [
        "delivery_failed",
        "completed",
    ]
    assert ("room-1", "msg-retry") in facade._terminal_delivery_logged


@pytest.mark.asyncio
async def test_delivery_start_timers_are_ttl_and_size_bounded():
    facade = make_delivery_facade(
        config=DeliveryConfig(
            delivery_started_ttl_seconds=0.01,
            delivery_started_cache_maxsize=2,
        )
    )

    for index in range(3):
        await facade.send_task_submitted(
            "room-1",
            f"msg-{index}",
            f"task-{index}",
            "Agent",
        )
    assert len(facade._delivery_started_at) == 2

    import asyncio

    await asyncio.sleep(0.02)
    assert len(facade._delivery_started_at) == 0


@pytest.mark.asyncio
async def test_lifecycle_start_stop_uses_delivery_surfaces():
    compat = FakeDeliveryCompat()
    fake_publisher = FakeEventPublisher()
    facade = _bind(compat=compat, event_publisher=fake_publisher)

    await facade.start()
    await facade.stop()

    assert compat.lifecycle_calls == [
        ("start", None),
        ("refresh_health", None),
        ("close_all_connections", None),
        ("stop", None),
    ]
    assert fake_publisher.lifecycle_calls == []
