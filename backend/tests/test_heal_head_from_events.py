"""Tests for RunCommandHandler.heal_head_from_events and watchdog integration."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from execution.run_command_handler import RunCommandHandler
from models.run import RunEventType, RunState


def _make_run_doc(
    run_id="run-1",
    room_id="room-1",
    state="processing",
    seq=1,
    trigger_message_id="msg-1",
):
    return {
        "run_id": run_id,
        "room_id": room_id,
        "state": state,
        "seq": seq,
        "trigger_message_id": trigger_message_id,
        "client_request_id": None,
    }


def _make_event(
    run_id="run-1",
    seq=2,
    event_type=RunEventType.RUN_COMPLETED.value,
    payload=None,
):
    return {
        "event_id": "evt-heal",
        "run_id": run_id,
        "seq": seq,
        "type": event_type,
        "payload": payload or {},
        "ts": datetime(2025, 6, 1, tzinfo=UTC),
    }


def _run_handler() -> tuple[MagicMock, MagicMock, object]:
    run_repo = MagicMock()
    event_repo = MagicMock()
    handler = RunCommandHandler(
        run_repository=run_repo,
        run_event_repository=event_repo,
    )
    return run_repo, event_repo, handler


@pytest.mark.asyncio
async def test_heal_no_run_doc_returns_false():
    """No run doc at all — nothing to heal."""
    run_repo, event_repo, handler = _run_handler()
    run_repo.find_one = AsyncMock(return_value=None)

    assert await handler.heal_head_from_events("run-missing") is False

    event_repo.find_one.assert_not_called()


@pytest.mark.asyncio
async def test_heal_no_newer_events_returns_false():
    """run_events has no events ahead of the head — nothing to heal."""
    run_repo, event_repo, handler = _run_handler()
    run_repo.find_one = AsyncMock(return_value=_make_run_doc(seq=2))
    event_repo.find_one = AsyncMock(return_value=None)

    assert await handler.heal_head_from_events("run-1") is False

    run_repo.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_heal_terminal_event_projects_forward():
    """run_events has a terminal RUN_COMPLETED at seq=3, head is at seq=1 — should heal."""
    run_repo, event_repo, handler = _run_handler()
    run_repo.find_one = AsyncMock(return_value=_make_run_doc(seq=1, state="processing"))
    event_repo.find_one = AsyncMock(
        return_value=_make_event(seq=3, event_type=RunEventType.RUN_COMPLETED.value)
    )
    run_repo.update_one = AsyncMock()

    result = await handler.heal_head_from_events("run-1")

    assert result is True
    run_repo.update_one.assert_called_once()
    call_args = run_repo.update_one.call_args
    filt = call_args[0][0]
    updates = call_args[0][1]["$set"]
    assert filt == {"run_id": "run-1"}
    assert updates["state"] == RunState.COMPLETED.value
    assert updates["seq"] == 3
    assert "ended_at" in updates
    assert "error_code" in updates


@pytest.mark.asyncio
async def test_heal_failed_event_with_payload():
    """run_events has a RUN_FAILED with error_code — error fields propagate to head."""
    run_repo, event_repo, handler = _run_handler()
    run_repo.find_one = AsyncMock(return_value=_make_run_doc(seq=1, state="processing"))
    event_repo.find_one = AsyncMock(
        return_value=_make_event(
            seq=2,
            event_type=RunEventType.RUN_FAILED.value,
            payload={"error_code": "TIMEOUT", "error_message": "stale"},
        )
    )
    run_repo.update_one = AsyncMock()

    result = await handler.heal_head_from_events("run-1")

    assert result is True
    updates = run_repo.update_one.call_args[0][1]["$set"]
    assert updates["state"] == RunState.FAILED.value
    assert updates["error_code"] == "TIMEOUT"
    assert updates["error_message"] == "stale"


@pytest.mark.asyncio
async def test_heal_active_event_projects_forward():
    """run_events has a RUN_STARTED at seq=2 but head is QUEUED at seq=0."""
    run_repo, event_repo, handler = _run_handler()
    run_repo.find_one = AsyncMock(return_value=_make_run_doc(seq=0, state="queued"))
    event_repo.find_one = AsyncMock(
        return_value=_make_event(seq=2, event_type=RunEventType.RUN_STARTED.value)
    )
    run_repo.update_one = AsyncMock()

    result = await handler.heal_head_from_events("run-1")

    assert result is True
    updates = run_repo.update_one.call_args[0][1]["$set"]
    assert updates["state"] == RunState.PROCESSING.value
    assert updates["seq"] == 2
    assert "ended_at" not in updates


@pytest.mark.asyncio
async def test_watchdog_heals_instead_of_appending():
    """When heal succeeds, append_run_timeout_failure should return None without
    calling _record_terminal."""
    run_repo, event_repo, handler = _run_handler()
    handler.heal_head_from_events = AsyncMock(return_value=True)
    handler._record_terminal = AsyncMock()

    result = await handler.append_run_timeout_failure(
        "room-1", "run-1", stale_minutes=90
    )

    assert result is None
    handler.heal_head_from_events.assert_called_once_with("run-1")
    handler._record_terminal.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_falls_through_when_no_divergence():
    """When heal returns False, the watchdog proceeds normally to _record_terminal."""
    run_repo, _event_repo, handler = _run_handler()
    handler.heal_head_from_events = AsyncMock(return_value=False)
    run_repo.find_one = AsyncMock(
        return_value=_make_run_doc(run_id="run-1", room_id="room-1")
    )
    handler._record_terminal = AsyncMock(return_value={"event_id": "e1"})

    result = await handler.append_run_timeout_failure(
        "room-1", "run-1", stale_minutes=90
    )

    assert result == {"event_id": "e1"}
    handler._record_terminal.assert_called_once()
    call_kw = handler._record_terminal.call_args[1]
    assert call_kw["terminal_state"] == RunState.FAILED
    assert call_kw["error_code"] == "RUN_TIMEOUT"
