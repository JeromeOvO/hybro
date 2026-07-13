import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.config import settings
from common.types import (
    Artifact,
    FileContent,
    FilePart,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from jobs.stale_task_checker import StaleRunWatchdogEventDeps, StaleTaskChecker
from models.room import MessageContent, RoomAgentMessage


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


@pytest.mark.asyncio
async def test_stale_recovery_persists_public_projection_without_inline_file_bytes():
    private_sentinel = "PRIVATE_SENTINEL_stale_recovery_raw_task"
    current_task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.completed),
        artifacts=[
            Artifact(
                artifact_id="artifact-1",
                name="response",
                parts=[
                    Part(root=TextPart(text="Recovered public result")),
                    Part(
                        root=FilePart(
                            file=FileContent(
                                bytes=private_sentinel,
                                mimeType="text/plain",
                                name="secret.txt",
                            ),
                            metadata={"s3_key": "artifacts/room/msg/secret.txt"},
                        )
                    ),
                ],
            )
        ],
        metadata={"hitl_prompt": private_sentinel},
    )
    existing_task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.working),
    )
    msg = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-message-1",
        user_id="user-1",
        agent_id="agent-1",
        agent_url="https://agent.example",
        has_task_tracking=True,
        message_content=MessageContent(message_task=existing_task),
    )
    store = SimpleNamespace(
        is_message_cancelled=AsyncMock(return_value=False),
        update_task_on_message=AsyncMock(return_value=True),
        touch_task_message=AsyncMock(),
    )
    checker = StaleTaskChecker()
    checker.set_runtime_deps(
        SimpleNamespace(
            store=store,
            rooms_collection=None,
            notify_task_update=AsyncMock(),
            increment_counter=MagicMock(),
            a2a_service=SimpleNamespace(
                get_agent_card_from_url=AsyncMock(return_value=SimpleNamespace())
            ),
        )
    )
    checker._get_task_from_agent = AsyncMock(return_value=current_task)

    async def convert_artifacts_side_effect(artifacts, **_kwargs):
        artifacts[0].parts[1].root.file.uri = "https://storage.example/secret.txt"

    with patch(
        "jobs.stale_task_checker.convert_pydantic_artifacts_to_s3",
        new=AsyncMock(side_effect=convert_artifacts_side_effect),
    ) as convert_artifacts:
        await checker._process_stale_task(msg)

    persisted = store.update_task_on_message.await_args.args[1]
    update_kwargs = store.update_task_on_message.await_args.kwargs
    convert_artifacts.assert_awaited_once()
    assert persisted["status"]["state"] == "completed"
    assert update_kwargs["message_text"] == "Recovered public result"
    assert persisted["metadata"] is None
    file_part = persisted["artifacts"][0]["parts"][1]
    file_root = file_part.get("root", file_part)
    assert file_root["file"] == {
        "uri": "https://storage.example/secret.txt",
        "mimeType": "text/plain",
        "name": "secret.txt",
    }
    assert private_sentinel not in json.dumps(persisted)
