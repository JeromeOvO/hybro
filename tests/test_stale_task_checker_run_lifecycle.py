from unittest.mock import AsyncMock, MagicMock

import pytest

from jobs.stale_task_checker import StaleTaskChecker


@pytest.mark.asyncio
async def test_watchdog_broadcasts_pre_recorded_payload_before_failed_status(monkeypatch):
    import jobs.stale_task_checker as mod
    import services.run_command_handler as handler_mod
    import services.sse_services as sse_mod

    calls: list[str] = []
    payload = {
        "event_id": "evt-timeout",
        "run_id": "run-1",
        "seq": 9,
        "type": "RUN_FAILED",
        "payload": {},
    }
    fake_sse = MagicMock()
    fake_sse.send_processing_status = AsyncMock(side_effect=lambda *a, **k: calls.append("send"))

    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    monkeypatch.setattr(
        mod.db_service,
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
    monkeypatch.setattr(
        handler_mod.run_command_handler,
        "append_run_timeout_failure",
        AsyncMock(return_value=payload),
    )
    monkeypatch.setattr(sse_mod, "sse_manager", fake_sse)
    monkeypatch.setattr(mod, "increment_counter", lambda name: calls.append("metric"))
    broadcast = AsyncMock(side_effect=lambda *a, **k: calls.append("broadcast"))
    monkeypatch.setattr(mod, "broadcast_run_event_payload", broadcast, raising=False)

    await StaleTaskChecker()._fail_stale_runs()

    broadcast.assert_awaited_once_with(
        "room-1",
        payload,
        client_request_id="cr-1",
        sse=fake_sse,
    )
    fake_sse.send_processing_status.assert_awaited_once()
    assert calls == ["metric", "broadcast", "send"]


@pytest.mark.asyncio
async def test_watchdog_payload_none_suppresses_metric_and_delivery(monkeypatch):
    import jobs.stale_task_checker as mod
    import services.run_command_handler as handler_mod
    import services.sse_services as sse_mod

    fake_sse = MagicMock()
    fake_sse.send_processing_status = AsyncMock()
    counter = MagicMock()

    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    monkeypatch.setattr(
        mod.db_service,
        "find_stale_non_terminal_runs",
        AsyncMock(return_value=[{"room_id": "room-1", "run_id": "run-1"}]),
    )
    monkeypatch.setattr(
        handler_mod.run_command_handler,
        "append_run_timeout_failure",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(sse_mod, "sse_manager", fake_sse)
    monkeypatch.setattr(mod, "increment_counter", counter)

    await StaleTaskChecker()._fail_stale_runs()

    counter.assert_not_called()
    fake_sse.send_processing_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_dual_write_disabled_sends_failed_without_lifecycle(
    monkeypatch,
):
    import jobs.stale_task_checker as mod
    import services.run_command_handler as handler_mod
    import services.sse_services as sse_mod
    from services.a2a_constants import SSEProcessingStatus

    calls: list[str] = []
    fake_sse = MagicMock()
    fake_sse.send_processing_status = AsyncMock(
        side_effect=lambda *a, **k: calls.append("send")
    )
    append = AsyncMock()
    broadcast = AsyncMock()

    monkeypatch.setenv("FEATURE_RUN_DUAL_WRITE", "0")
    monkeypatch.setattr(
        mod.db_service,
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
    monkeypatch.setattr(
        handler_mod.run_command_handler,
        "append_run_timeout_failure",
        append,
    )
    monkeypatch.setattr(sse_mod, "sse_manager", fake_sse)
    monkeypatch.setattr(mod, "broadcast_run_event_payload", broadcast, raising=False)
    monkeypatch.setattr(mod, "increment_counter", lambda name: calls.append(name))

    await StaleTaskChecker()._fail_stale_runs()

    assert calls == ["run_watchdog_forced_failure_total", "send"]
    fake_sse.send_processing_status.assert_awaited_once_with(
        "room-1",
        SSEProcessingStatus.FAILED,
        "msg-1",
        client_request_id="cr-1",
        details="Run watchdog: stale non-terminal run timed out",
    )
    append.assert_not_awaited()
    broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_dual_write_disabled_uses_shared_feature_helper(monkeypatch):
    import jobs.stale_task_checker as mod
    import services.run_command_handler as handler_mod
    import services.sse_services as sse_mod

    fake_sse = MagicMock()
    fake_sse.send_processing_status = AsyncMock()
    append = AsyncMock()
    broadcast = AsyncMock()

    monkeypatch.setenv("FEATURE_RUN_DUAL_WRITE", "1")
    monkeypatch.setattr(
        mod.db_service,
        "find_stale_non_terminal_runs",
        AsyncMock(return_value=[{"room_id": "room-1", "run_id": "run-1"}]),
    )
    monkeypatch.setattr(mod, "feature_run_dual_write_enabled", lambda: False)
    monkeypatch.setattr(
        handler_mod.run_command_handler,
        "append_run_timeout_failure",
        append,
    )
    monkeypatch.setattr(sse_mod, "sse_manager", fake_sse)
    monkeypatch.setattr(mod, "broadcast_run_event_payload", broadcast, raising=False)

    await StaleTaskChecker()._fail_stale_runs()

    fake_sse.send_processing_status.assert_awaited_once()
    append.assert_not_awaited()
    broadcast.assert_not_awaited()
