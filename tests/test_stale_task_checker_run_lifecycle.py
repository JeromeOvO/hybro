from unittest.mock import AsyncMock, MagicMock

import pytest

from common.config import settings
from jobs.stale_task_checker import StaleRunWatchdogEventDeps, StaleTaskChecker


@pytest.mark.asyncio
async def test_watchdog_broadcasts_pre_recorded_payload_before_failed_status(monkeypatch):
    import jobs.stale_task_checker as mod

    calls: list[str] = []
    payload = {
        "event_id": "evt-timeout",
        "run_id": "run-1",
        "seq": 9,
        "type": "RUN_FAILED",
        "payload": {},
    }
    append_timeout = AsyncMock(return_value=payload)
    emit_run_event = AsyncMock(side_effect=lambda *a, **k: calls.append("broadcast"))
    emit_status = AsyncMock(side_effect=lambda *a, **k: calls.append("send"))

    monkeypatch.setattr(settings, "feature_run_dual_write", True)
    monkeypatch.setattr(
        mod.store,
        "find_stale_non_terminal_runs",
        AsyncMock(
            return_value=[
                {
                    "room_id": "room-1",
                    "run_id": "run-1",
                    "trigger_message_id": "msg-1",
                    "client_request_id": "cr-1",
                }
            ]
        ),
    )
    monkeypatch.setattr(mod, "increment_counter", lambda name: calls.append("metric"))
    checker = StaleTaskChecker()
    checker.set_run_watchdog_event_deps(
        StaleRunWatchdogEventDeps(
            append_run_timeout_failure=append_timeout,
            emit_run_event=emit_run_event,
            emit_processing_status=emit_status,
            run_dual_write_enabled=lambda: True,
        )
    )

    await checker._fail_stale_runs()

    append_timeout.assert_awaited_once_with("room-1", "run-1", stale_minutes=90)
    emit_run_event.assert_awaited_once_with(
        room_id="room-1",
        payload=payload,
        client_request_id="cr-1",
    )
    emit_status.assert_awaited_once()
    assert calls == ["metric", "broadcast", "send"]


@pytest.mark.asyncio
async def test_watchdog_payload_none_suppresses_metric_and_delivery(monkeypatch):
    import jobs.stale_task_checker as mod

    append_timeout = AsyncMock(return_value=None)
    emit_run_event = AsyncMock()
    emit_status = AsyncMock()
    counter = MagicMock()

    monkeypatch.setattr(settings, "feature_run_dual_write", True)
    monkeypatch.setattr(
        mod.store,
        "find_stale_non_terminal_runs",
        AsyncMock(return_value=[{"room_id": "room-1", "run_id": "run-1"}]),
    )
    monkeypatch.setattr(mod, "increment_counter", counter)
    checker = StaleTaskChecker()
    checker.set_run_watchdog_event_deps(
        StaleRunWatchdogEventDeps(
            append_run_timeout_failure=append_timeout,
            emit_run_event=emit_run_event,
            emit_processing_status=emit_status,
            run_dual_write_enabled=lambda: True,
        )
    )

    await checker._fail_stale_runs()

    counter.assert_not_called()
    emit_run_event.assert_not_awaited()
    emit_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_dual_write_disabled_sends_failed_without_lifecycle(
    monkeypatch,
):
    import jobs.stale_task_checker as mod

    calls: list[str] = []
    append = AsyncMock()
    emit_run_event = AsyncMock()
    emit_status = AsyncMock(side_effect=lambda *a, **k: calls.append("send"))

    monkeypatch.setattr(settings, "feature_run_dual_write", False)
    monkeypatch.setattr(
        mod.store,
        "find_stale_non_terminal_runs",
        AsyncMock(
            return_value=[
                {
                    "room_id": "room-1",
                    "run_id": "run-1",
                    "trigger_message_id": "msg-1",
                    "client_request_id": "cr-1",
                }
            ]
        ),
    )
    monkeypatch.setattr(mod, "increment_counter", lambda name: calls.append(name))
    checker = StaleTaskChecker()
    checker.set_run_watchdog_event_deps(
        StaleRunWatchdogEventDeps(
            append_run_timeout_failure=append,
            emit_run_event=emit_run_event,
            emit_processing_status=emit_status,
            run_dual_write_enabled=lambda: False,
        )
    )

    await checker._fail_stale_runs()

    assert calls == ["run_watchdog_forced_failure_total", "send"]
    emit_status.assert_awaited_once_with(
        room_id="room-1",
        status="failed",
        message_id="msg-1",
        client_request_id="cr-1",
        details="Run watchdog: stale non-terminal run timed out",
    )
    append.assert_not_awaited()
    emit_run_event.assert_not_awaited()
