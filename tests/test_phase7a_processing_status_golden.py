import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app_shell.delivery_runtime import SSEManager
from common.dto import RunEventNotification
from execution.orchestration.room_message_center import RoomMessageCenter
from models.supervisor import RunStatus, SupervisorRunResult, SupervisorTrajectory
from tests.delivery_adapter_fakes import make_bound_manager

ROOT = Path(__file__).resolve().parents[1]


async def _next_sse_type(conn) -> tuple[str, dict]:
    raw = await conn.queue.get()
    parsed = json.loads(raw)
    return parsed["type"], parsed["data"]


async def _drain_sse(conn) -> list[tuple[str, dict]]:
    items: list[tuple[str, dict]] = []
    while not conn.queue.empty():
        items.append(await _next_sse_type(conn))
    return items


def _make_rmc_for_supervisor_result(manager: SSEManager) -> RoomMessageCenter:
    rmc = object.__new__(RoomMessageCenter)
    rmc.sse_manager = manager
    rmc.database_service = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(
            return_value=SimpleNamespace(extend_info={})
        ),
        update_room_user_message_by_message_id=AsyncMock(),
        get_room_by_room_id=AsyncMock(return_value=SimpleNamespace(extend_info={})),
        update_room_by_room_id=AsyncMock(),
    )
    rmc._store = rmc.database_service
    rmc.room_coordinator_service = SimpleNamespace(
        emit_synthesis_message=AsyncMock()
    )
    rmc._emit_unified_summary = AsyncMock()
    rmc._trigger_compaction_safe = AsyncMock()
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc.build_turn_content = None
    rmc.supervisor_planning_error_cls = RuntimeError
    rmc._turn_event_appender = None
    return rmc


def _bind_test_processing_emitter(
    rmc: RoomMessageCenter,
    manager: SSEManager,
    record: AsyncMock,
) -> None:
    async def emit_processing_status(
        *,
        room_id: str,
        status,
        message_id: str,
        lifecycle_message_id: str | None = None,
        record_lifecycle: bool = True,
        client_request_id: str | None = None,
        details=None,
        **_kwargs,
    ) -> None:
        if record_lifecycle:
            payload = await record(
                room_id=room_id,
                status=getattr(status, "value", status),
                message_id=lifecycle_message_id or message_id,
                client_request_id=client_request_id,
                details=details,
            )
            if payload:
                await manager._emit_event(
                    RunEventNotification(
                        room_id=room_id,
                        event_id=payload.get("event_id"),
                        run_id=payload.get("run_id"),
                        seq=payload.get("seq"),
                        run_event_type=payload.get("type"),
                        payload=payload.get("payload") or {},
                        correlation_id=client_request_id,
                    )
                )
        await manager.send_processing_status(
            room_id,
            getattr(status, "value", status),
            message_id,
            details=details,
            client_request_id=client_request_id,
        )

    rmc._processing_status_emitter = emit_processing_status


@pytest.mark.asyncio
async def test_golden_send_message_processing_status_order(monkeypatch):
    import app_shell.room_runtime as room_services
    from common.a2a_constants import SSEProcessingStatus
    from execution.events import emit_processing_status

    manager = make_bound_manager()
    conn = await manager.add_connection("room-1")
    payload = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 2,
        "type": "RUN_STARTED",
        "payload": {},
    }
    record = AsyncMock(side_effect=[payload, None])
    resolver = SimpleNamespace(
        resolve_client_request_id=AsyncMock(return_value="cr-1")
    )
    run_lifecycle = SimpleNamespace(record_processing_status=record)

    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")

    svc = object.__new__(room_services.RoomServices)
    svc._processing_status_emitter = lambda **kwargs: emit_processing_status(
        **kwargs,
        run_lifecycle=run_lifecycle,
        event_publisher=manager._facade.event_publisher,
        run_event_enabled=lambda: True,
        client_request_id_resolver=resolver,
    )
    await svc._send_processing_status("room-1", "msg-1", "cr-1")

    first_type, first_data = await _next_sse_type(conn)
    second_type, second_data = await _next_sse_type(conn)

    assert record.await_count == 1
    assert first_type == "run_event"
    assert first_data["event_id"] == "evt-1"
    assert first_data["correlation_id"] == "cr-1"
    assert second_type == "processing_status"
    assert second_data["status"] == SSEProcessingStatus.PROCESSING
    assert second_data["message_id"] == "msg-1"
    assert second_data["client_request_id"] == "cr-1"


@pytest.mark.asyncio
async def test_golden_hitl_resolve_resume_completion_order(monkeypatch):
    manager = make_bound_manager()
    conn = await manager.add_connection("room-1")
    payload = {
        "event_id": "evt-2",
        "run_id": "msg-1",
        "seq": 7,
        "type": "RUN_COMPLETED",
        "payload": {},
    }
    record = AsyncMock(side_effect=[payload, None])

    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")

    rmc = object.__new__(RoomMessageCenter)
    rmc.sse_manager = manager
    _bind_test_processing_emitter(rmc, manager, record)
    rmc.database_service = SimpleNamespace(
        save_continuation_on_message=AsyncMock(),
        get_room_by_room_id=AsyncMock(return_value=SimpleNamespace(extend_info={})),
    )
    rmc._store = rmc.database_service
    rmc.queue_executor = SimpleNamespace(
        resume_from_continuation=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                needs_completion=True,
                room_id="room-1",
                user_message_id="msg-1",
            )
        )
    )
    rmc._emit_unified_summary = AsyncMock()
    rmc._log_room_memory_stats = AsyncMock()

    result = await rmc._resume_continuation_locked(
        {"supervisor": False, "room_id": "room-1"},
        "agent-msg-1",
        "answer",
    )

    first_type, first_data = await _next_sse_type(conn)
    second_type, second_data = await _next_sse_type(conn)

    assert result is True
    assert record.await_count == 1
    assert first_type == "run_event"
    assert first_data["event_id"] == "evt-2"
    assert second_type == "processing_status"
    assert second_data["status"] == "completed"
    assert second_data["message_id"] == "msg-1"


@pytest.mark.asyncio
async def test_golden_duplicate_terminal_root_completion_suppressed(monkeypatch):
    manager = make_bound_manager()
    conn = await manager.add_connection("room-1")
    payload = {
        "event_id": "evt-terminal",
        "run_id": "msg-1",
        "seq": 10,
        "type": "RUN_COMPLETED",
        "payload": {},
    }
    record = AsyncMock(side_effect=[payload, None])

    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")

    rmc = _make_rmc_for_supervisor_result(manager)
    _bind_test_processing_emitter(rmc, manager, record)
    result = SupervisorRunResult(
        status=RunStatus.COMPLETED,
        trajectory=SupervisorTrajectory(),
    )

    await rmc._handle_supervisor_run_result(result, "room-1", "msg-1")
    await rmc._handle_supervisor_run_result(result, "room-1", "msg-1")

    frames = await _drain_sse(conn)
    assert [kind for kind, _data in frames] == ["run_event", "processing_status"]
    assert frames[0][1]["event_id"] == "evt-terminal"
    assert frames[1][1]["status"] == "completed"
    assert frames[1][1]["message_id"] == "msg-1"
    assert record.await_count == 2


@pytest.mark.asyncio
async def test_golden_duplicate_terminal_suppressed_across_redis_l2(monkeypatch):
    from tests.test_sse_event_broker import MockRedisService

    redis = MockRedisService()
    first_manager = make_bound_manager(redis_service=redis, instance_id="worker-1")
    second_manager = make_bound_manager(redis_service=redis, instance_id="worker-2")
    first_conn = await first_manager.add_connection("room-1")
    second_conn = await second_manager.add_connection("room-1")

    payload = {
        "event_id": "evt-terminal",
        "run_id": "msg-1",
        "seq": 10,
        "type": "RUN_COMPLETED",
        "payload": {},
    }
    record = AsyncMock(side_effect=[payload, None])

    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")

    result = SupervisorRunResult(
        status=RunStatus.COMPLETED,
        trajectory=SupervisorTrajectory(),
    )
    first_rmc = _make_rmc_for_supervisor_result(first_manager)
    second_rmc = _make_rmc_for_supervisor_result(second_manager)
    _bind_test_processing_emitter(first_rmc, first_manager, record)
    _bind_test_processing_emitter(second_rmc, second_manager, record)
    await first_rmc._handle_supervisor_run_result(result, "room-1", "msg-1")
    await second_rmc._handle_supervisor_run_result(result, "room-1", "msg-1")

    first_frames = await _drain_sse(first_conn)
    second_frames = await _drain_sse(second_conn)
    assert [kind for kind, _data in first_frames] == [
        "run_event",
        "processing_status",
    ]
    assert second_frames == []
    assert redis._store["terminal:room-1:msg-1"] == "completed"
    assert record.await_count == 2


@pytest.mark.asyncio
async def test_golden_clarifying_soft_complete_is_transport_only(monkeypatch):
    manager = make_bound_manager()
    conn = await manager.add_connection("room-1")
    lifecycle = AsyncMock()

    turn_appender = SimpleNamespace(append=AsyncMock())
    rmc = _make_rmc_for_supervisor_result(manager)
    _bind_test_processing_emitter(rmc, manager, lifecycle)
    rmc._turn_event_appender = turn_appender
    result = SupervisorRunResult(
        status=RunStatus.CLARIFYING,
        trajectory=SupervisorTrajectory(),
        clarification_question="Which account should I use?",
    )

    await rmc._handle_supervisor_run_result(result, "room-1", "msg-1")

    frames = await _drain_sse(conn)
    assert [kind for kind, _data in frames] == ["processing_status"]
    assert frames[0][1]["status"] == "completed"
    lifecycle.assert_not_awaited()
    turn_appender.append.assert_awaited_once_with(
        "room-1", "msg-1", "turn_completed", {"duration_ms": 0}
    )


@pytest.mark.asyncio
async def test_golden_clarify_resume_retry_failure_completed_is_transport_only(
    monkeypatch,
):
    from execution.orchestration.room_supervisor_service import SupervisorPlanningError
    from models.supervisor import AgentProfile, RoomConfig

    manager = make_bound_manager()
    conn = await manager.add_connection("room-1")
    lifecycle = AsyncMock()

    trajectory = SupervisorTrajectory()
    trajectory.clarify_user_reply = "Use account A"
    user_message = SimpleNamespace(
        extend_info={
            "agent_registry": [
                AgentProfile(agent_id="agent-1", agent_name="Agent").model_dump()
            ],
            "room_config": RoomConfig().model_dump(),
            "supervisor_clarify_resume": True,
            "resumed_trajectory": trajectory.model_dump(mode="json"),
            "clarify_original_message_id": "original-msg",
        },
        message_content=SimpleNamespace(message_text="Use account A", attachments=None),
    )
    original_msg = SimpleNamespace(extend_info={"supervisor_trajectory": {}})
    rmc = _make_rmc_for_supervisor_result(manager)
    _bind_test_processing_emitter(rmc, manager, lifecycle)
    rmc.database_service.get_room_user_message_by_message_id = AsyncMock(
        return_value=original_msg
    )
    rmc.supervisor_executor = SimpleNamespace(
        run=AsyncMock(side_effect=SupervisorPlanningError("retry failed"))
    )
    rmc.supervisor_planning_error_cls = SupervisorPlanningError
    rmc._persist_failed_trajectory = AsyncMock()
    rmc._log_room_memory_stats = AsyncMock()

    response = await rmc._process_supervisor(
        user_message=user_message,
        room_id="room-1",
        room_user_message_id="retry-msg",
        user_id="user-1",
        quoted_text=None,
        token=None,
    )

    frames = await _drain_sse(conn)
    assert response.success is False
    assert [kind for kind, _data in frames] == ["processing_status"]
    assert frames[0][1]["status"] == "completed"
    assert frames[0][1]["message_id"] == "retry-msg"
    lifecycle.assert_not_awaited()


def test_clarifying_path_emits_turn_completed_via_turn_event_appender():
    """CLARIFYING must append turn_completed before completed processing_status."""
    text = (
        ROOT / "execution/orchestration/room_message_center.py"
    ).read_text(encoding="utf-8")
    assert "RunStatus.CLARIFYING" in text
    assert "_turn_event_appender.append" in text
    assert '"turn_completed"' in text
    # turn_completed for CLARIFYING must precede COMPLETED processing_status emit.
    clarifying = text.split("case RunStatus.CLARIFYING:")[1].split("case RunStatus.")[0]
    assert "turn_completed" in clarifying
    assert "SSEProcessingStatus.COMPLETED" in clarifying
    assert clarifying.index("turn_completed") < clarifying.index(
        "SSEProcessingStatus.COMPLETED"
    )


@pytest.mark.asyncio
async def test_supervisor_completed_emits_turn_completion_kind_in_details():
    """COMPLETED processing_status must include turn_completion_kind in details."""
    from models.supervisor import ActionType, TrajectoryEntry, SupervisorAction, StepResult
    from datetime import datetime, timezone

    manager = make_bound_manager()
    conn = await manager.add_connection("room-1")
    rmc = _make_rmc_for_supervisor_result(manager)
    rmc._emit_deterministic_digest = AsyncMock()
    rmc._run_supervisor_terminal_post_loop_integration = AsyncMock()
    record = AsyncMock(return_value=None)
    _bind_test_processing_emitter(rmc, manager, record)

    result = SupervisorRunResult(
        status=RunStatus.COMPLETED,
        trajectory=SupervisorTrajectory(
            entries=[
                TrajectoryEntry(
                    step_number=1,
                    action=SupervisorAction(action=ActionType.DELEGATE, reasoning="test"),
                    results=[StepResult(
                        step_number=1,
                        agent_id="agent-a",
                        agent_name="a",
                        task="do something",
                        response_text="answer",
                        success=True,
                    )],
                    started_at=datetime.now(timezone.utc),
                )
            ],
        ),
        synthesis_text=None,
    )
    await rmc._handle_supervisor_run_result(
        room_id="room-1",
        user_message_id="msg-1",
        result=result,
        room=SimpleNamespace(room_id="room-1", extend_info={}),
        user_message=SimpleNamespace(extend_info={}),
    )

    db = rmc.database_service
    db.update_room_user_message_by_message_id.assert_called()
    persisted_msg = db.update_room_user_message_by_message_id.call_args_list[-1][0][1]
    assert persisted_msg.extend_info["turn_completion_kind"] == "deterministic"

    frames = await _drain_sse(conn)
    completed_frames = [
        (kind, data) for kind, data in frames if kind == "processing_status" and data.get("status") == "completed"
    ]
    assert len(completed_frames) == 1
    assert completed_frames[0][1]["details"]["turn_completion_kind"] == "deterministic"


@pytest.mark.asyncio
async def test_supervisor_synthesis_completed_emits_synthesis_kind():
    """Synthesis path emits turn_completion_kind='synthesis' in details."""
    from models.supervisor import ActionType, TrajectoryEntry, SupervisorAction, StepResult
    from datetime import datetime, timezone

    manager = make_bound_manager()
    conn = await manager.add_connection("room-1")
    rmc = _make_rmc_for_supervisor_result(manager)
    rmc._run_supervisor_terminal_post_loop_integration = AsyncMock()
    record = AsyncMock(return_value=None)
    _bind_test_processing_emitter(rmc, manager, record)

    result = SupervisorRunResult(
        status=RunStatus.COMPLETED,
        trajectory=SupervisorTrajectory(
            entries=[
                TrajectoryEntry(
                    step_number=1,
                    action=SupervisorAction(action=ActionType.DELEGATE, reasoning="test"),
                    results=[StepResult(
                        step_number=1,
                        agent_id="agent-a",
                        agent_name="a",
                        task="do something",
                        response_text="answer",
                        success=True,
                    )],
                    started_at=datetime.now(timezone.utc),
                )
            ],
        ),
        synthesis_text="Here is the combined answer...",
    )
    await rmc._handle_supervisor_run_result(
        room_id="room-1",
        user_message_id="msg-1",
        result=result,
        room=SimpleNamespace(room_id="room-1", extend_info={}),
        user_message=SimpleNamespace(extend_info={}),
    )

    db = rmc.database_service
    persisted_msg = db.update_room_user_message_by_message_id.call_args_list[-1][0][1]
    assert persisted_msg.extend_info["turn_completion_kind"] == "synthesis"

    frames = await _drain_sse(conn)
    completed_frames = [
        (kind, data) for kind, data in frames if kind == "processing_status" and data.get("status") == "completed"
    ]
    assert len(completed_frames) == 1
    assert completed_frames[0][1]["details"]["turn_completion_kind"] == "synthesis"
