import json
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.config import settings
from common.dto import RunEventNotification
from common.utils.cancellation import CancellationToken
from delivery.facade import DeliveryFacade
from execution.orchestration.room_message_center import RoomMessageCenter
from models.supervisor import RunStatus, SupervisorRunResult, SupervisorTrajectory
from tests.fakes.delivery import make_delivery_facade

ROOT = Path(__file__).resolve().parents[1]


async def _next_sse_type(conn) -> tuple[str, dict]:
    frame = await conn.queue.get()
    if isinstance(frame, str):
        frame = json.loads(frame)
    return frame["type"], frame["data"]


async def _drain_sse(conn) -> list[tuple[str, dict]]:
    items: list[tuple[str, dict]] = []
    while not conn.queue.empty():
        items.append(await _next_sse_type(conn))
    return items


def _make_rmc_for_supervisor_result(delivery: DeliveryFacade) -> RoomMessageCenter:
    rmc = object.__new__(RoomMessageCenter)
    rmc.delivery = delivery
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(
            return_value=SimpleNamespace(extend_info={})
        ),
    )
    rmc.message_writer = SimpleNamespace(
        update_room_user_message_by_message_id=AsyncMock(),
        cancel_descendants=AsyncMock(),
        cancel_agent_messages_by_ids=AsyncMock(),
    )
    rmc.room_reader = SimpleNamespace(
        get_room_by_room_id=AsyncMock(return_value=SimpleNamespace(extend_info={})),
    )
    rmc.room_writer = SimpleNamespace(
        update_room_by_room_id=AsyncMock(),
    )
    rmc.coordinator = SimpleNamespace(emit_synthesis_message=AsyncMock())
    rmc._emit_unified_summary = AsyncMock(return_value=("synthesis", "summary content"))
    rmc._trigger_compaction_safe = AsyncMock()
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc.build_turn_content = None
    rmc.supervisor_planning_error_cls = RuntimeError
    rmc._turn_event_appender = None
    return rmc


def _bind_test_processing_emitter(
    rmc: RoomMessageCenter,
    delivery: DeliveryFacade,
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
    ) -> dict | None:
        payload = None
        if record_lifecycle:
            payload = await record(
                room_id=room_id,
                status=getattr(status, "value", status),
                message_id=lifecycle_message_id or message_id,
                client_request_id=client_request_id,
                details=details,
            )
            if payload:
                await delivery.emit(
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
        await delivery.send_processing_status(
            room_id,
            getattr(status, "value", status),
            message_id,
            details=details,
            client_request_id=client_request_id,
        )
        return payload

    rmc._processing_status_emitter = emit_processing_status


@pytest.mark.asyncio
async def test_golden_execution_preflight_processing_status_order(monkeypatch):
    from common.a2a_constants import SSEProcessingStatus
    from common.dto import ExecutionAck, ExecutionRequest
    from execution.facade import ExecutionFacade

    delivery = make_delivery_facade()
    conn = await delivery.add_connection("room-1")
    payload = {
        "event_id": "evt-1",
        "run_id": "root-msg-1",
        "seq": 2,
        "type": "RUN_STARTED",
        "payload": {},
    }
    record = AsyncMock(side_effect=[payload, None])
    resolver = SimpleNamespace(resolve_client_request_id=AsyncMock(return_value="cr-1"))
    run_lifecycle = SimpleNamespace(record_processing_status=record)

    monkeypatch.setattr(settings, "feature_run_event_sse", True)

    facade = object.__new__(ExecutionFacade)
    facade._run_lifecycle = run_lifecycle
    facade._event_publisher = delivery.event_publisher
    facade._run_event_enabled = lambda: True
    facade._client_request_id_resolver = resolver
    request = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        client_request_id="cr-1",
    )
    ack = ExecutionAck(
        room_id="room-1",
        message_id="msg-1",
        dispatch_root_message_id="root-msg-1",
    )

    await facade._emit_room_preflight_processing_status(request, ack)

    first_type, first_data = await _next_sse_type(conn)
    second_type, second_data = await _next_sse_type(conn)

    assert record.await_count == 1
    assert first_type == "run_event"
    assert first_data["event_id"] == "evt-1"
    assert first_data["correlation_id"] == "cr-1"
    assert first_data["run_id"] == "root-msg-1"
    assert second_type == "processing_status"
    assert second_data["status"] == SSEProcessingStatus.PROCESSING
    assert second_data["message_id"] == "msg-1"
    assert second_data["related_message_id"] == "root-msg-1"
    assert second_data["client_request_id"] == "cr-1"


@pytest.mark.asyncio
async def test_golden_hitl_resolve_resume_completion_order(monkeypatch):
    delivery = make_delivery_facade()
    conn = await delivery.add_connection("room-1")
    payload = {
        "event_id": "evt-2",
        "run_id": "msg-1",
        "seq": 7,
        "type": "RUN_COMPLETED",
        "payload": {},
    }
    record = AsyncMock(side_effect=[payload, None])

    monkeypatch.setattr(settings, "feature_run_event_sse", True)

    rmc = object.__new__(RoomMessageCenter)
    rmc.delivery = delivery
    token = CancellationToken(message_id="msg-1")
    rmc.cancellation_control = SimpleNamespace(
        check_cancelled=AsyncMock(return_value=False),
        release_token=MagicMock(return_value=True),
    )
    _bind_test_processing_emitter(rmc, delivery, record)
    rmc.continuation_store = SimpleNamespace(
        save_continuation_on_message=AsyncMock(),
    )
    rmc.room_reader = SimpleNamespace(
        get_room_by_room_id=AsyncMock(return_value=SimpleNamespace(extend_info={})),
    )
    rmc.queue_executor = SimpleNamespace(
        resume_from_continuation=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                needs_completion=True,
                room_id="room-1",
                user_message_id="msg-1",
                token=token,
            )
        )
    )
    rmc._emit_unified_summary = AsyncMock(return_value=("synthesis", "summary content"))
    rmc._persist_turn_completion_kind = AsyncMock()
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
    assert second_data["details"]["turn_completion_kind"] == "synthesis"
    assert second_data["details"]["turn_phase"] == "terminal"


@pytest.mark.asyncio
async def test_resume_completion_uses_deterministic_kind_when_summary_skipped(
    monkeypatch,
):
    delivery = make_delivery_facade()
    conn = await delivery.add_connection("room-1")
    record = AsyncMock(
        return_value={
            "event_id": "evt-deterministic",
            "run_id": "msg-1",
            "seq": 8,
            "type": "RUN_COMPLETED",
            "payload": {},
        }
    )

    monkeypatch.setattr(settings, "feature_run_event_sse", True)

    rmc = object.__new__(RoomMessageCenter)
    rmc.delivery = delivery
    token = CancellationToken(message_id="msg-1")
    rmc.cancellation_control = SimpleNamespace(
        check_cancelled=AsyncMock(return_value=False),
        release_token=MagicMock(return_value=True),
    )
    _bind_test_processing_emitter(rmc, delivery, record)
    rmc.continuation_store = SimpleNamespace(
        save_continuation_on_message=AsyncMock(),
    )
    rmc.room_reader = SimpleNamespace(
        get_room_by_room_id=AsyncMock(return_value=SimpleNamespace(extend_info={})),
    )
    rmc.queue_executor = SimpleNamespace(
        resume_from_continuation=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                needs_completion=True,
                room_id="room-1",
                user_message_id="msg-1",
                token=token,
            )
        )
    )
    rmc._emit_unified_summary = AsyncMock(return_value=("deterministic", None))
    rmc._persist_turn_completion_kind = AsyncMock()
    rmc._log_room_memory_stats = AsyncMock()

    result = await rmc._resume_continuation_locked(
        {"supervisor": False, "room_id": "room-1"},
        "agent-msg-1",
        "answer",
    )

    frames = await _drain_sse(conn)
    completed_frames = [
        (kind, data)
        for kind, data in frames
        if kind == "processing_status" and data.get("status") == "completed"
    ]

    assert result is True
    assert len(completed_frames) == 1
    assert completed_frames[0][1]["details"]["turn_completion_kind"] == "deterministic"
    assert completed_frames[0][1]["details"]["turn_phase"] == "terminal"
    rmc._persist_turn_completion_kind.assert_not_awaited()


@pytest.mark.asyncio
async def test_emit_summary_working_includes_turn_phase(monkeypatch):
    delivery = make_delivery_facade()
    conn = await delivery.add_connection("room-1")
    record = AsyncMock(return_value=None)

    rmc = object.__new__(RoomMessageCenter)
    rmc.delivery = delivery
    _bind_test_processing_emitter(rmc, delivery, record)

    await rmc._emit_summary_working(
        room_id="room-1",
        user_message_id="msg-1",
        summary_message_id="summary-msg-1",
        summary_client_request_id="cr-1",
    )

    frames = await _drain_sse(conn)
    processing_frames = [
        (kind, data)
        for kind, data in frames
        if kind == "processing_status" and data.get("status") == "processing"
    ]
    assert len(processing_frames) == 1
    assert processing_frames[0][1]["details"]["turn_phase"] == "synthesizing"
    assert "Compiling summary" in processing_frames[0][1]["details"]["message"]


@pytest.mark.asyncio
async def test_golden_duplicate_terminal_root_completion_suppressed(monkeypatch):
    delivery = make_delivery_facade()
    conn = await delivery.add_connection("room-1")
    payload = {
        "event_id": "evt-terminal",
        "run_id": "msg-1",
        "seq": 10,
        "type": "RUN_COMPLETED",
        "payload": {},
    }
    record = AsyncMock(side_effect=[payload, None])

    monkeypatch.setattr(settings, "feature_run_event_sse", True)

    rmc = _make_rmc_for_supervisor_result(delivery)
    _bind_test_processing_emitter(rmc, delivery, record)
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
    first_delivery = make_delivery_facade(redis_service=redis, instance_id="worker-1")
    second_delivery = make_delivery_facade(redis_service=redis, instance_id="worker-2")
    first_conn = await first_delivery.add_connection("room-1")
    second_conn = await second_delivery.add_connection("room-1")

    payload = {
        "event_id": "evt-terminal",
        "run_id": "msg-1",
        "seq": 10,
        "type": "RUN_COMPLETED",
        "payload": {},
    }
    record = AsyncMock(side_effect=[payload, None])

    monkeypatch.setattr(settings, "feature_run_event_sse", True)

    result = SupervisorRunResult(
        status=RunStatus.COMPLETED,
        trajectory=SupervisorTrajectory(),
    )
    first_rmc = _make_rmc_for_supervisor_result(first_delivery)
    second_rmc = _make_rmc_for_supervisor_result(second_delivery)
    _bind_test_processing_emitter(first_rmc, first_delivery, record)
    _bind_test_processing_emitter(second_rmc, second_delivery, record)
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
async def test_supervisor_completed_emits_turn_completion_kind_in_details():
    """COMPLETED processing_status must include turn_completion_kind in details."""
    from datetime import datetime

    from models.supervisor import (
        ActionType,
        StepResult,
        SupervisorAction,
        TrajectoryEntry,
    )

    delivery = make_delivery_facade()
    conn = await delivery.add_connection("room-1")
    rmc = _make_rmc_for_supervisor_result(delivery)
    rmc._emit_deterministic_digest = AsyncMock()
    rmc._run_supervisor_terminal_post_loop_integration = AsyncMock()
    record = AsyncMock(
        return_value={
            "event_id": "evt-supervisor-deterministic",
            "run_id": "msg-1",
            "seq": 8,
            "type": "RUN_COMPLETED",
            "payload": {},
        }
    )
    _bind_test_processing_emitter(rmc, delivery, record)

    result = SupervisorRunResult(
        status=RunStatus.COMPLETED,
        trajectory=SupervisorTrajectory(
            entries=[
                TrajectoryEntry(
                    step_number=1,
                    action=SupervisorAction(
                        action=ActionType.DELEGATE, reasoning="test"
                    ),
                    results=[
                        StepResult(
                            step_number=1,
                            agent_id="agent-a",
                            agent_name="a",
                            task="do something",
                            response_text="answer",
                            success=True,
                        )
                    ],
                    started_at=datetime.now(UTC),
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

    frames = await _drain_sse(conn)
    completed_frames = [
        (kind, data)
        for kind, data in frames
        if kind == "processing_status" and data.get("status") == "completed"
    ]
    assert len(completed_frames) == 1
    assert completed_frames[0][1]["details"]["turn_completion_kind"] == "deterministic"
    assert completed_frames[0][1]["details"]["turn_phase"] == "terminal"


@pytest.mark.asyncio
async def test_supervisor_synthesis_completed_emits_synthesis_kind():
    """Synthesis path emits turn_completion_kind='synthesis' in details."""
    from datetime import datetime

    from models.supervisor import (
        ActionType,
        StepResult,
        SupervisorAction,
        TrajectoryEntry,
    )

    delivery = make_delivery_facade()
    conn = await delivery.add_connection("room-1")
    rmc = _make_rmc_for_supervisor_result(delivery)
    rmc._run_supervisor_terminal_post_loop_integration = AsyncMock()
    record = AsyncMock(
        return_value={
            "event_id": "evt-supervisor-synthesis",
            "run_id": "msg-1",
            "seq": 8,
            "type": "RUN_COMPLETED",
            "payload": {},
        }
    )
    _bind_test_processing_emitter(rmc, delivery, record)

    result = SupervisorRunResult(
        status=RunStatus.COMPLETED,
        trajectory=SupervisorTrajectory(
            entries=[
                TrajectoryEntry(
                    step_number=1,
                    action=SupervisorAction(
                        action=ActionType.DELEGATE, reasoning="test"
                    ),
                    results=[
                        StepResult(
                            step_number=1,
                            agent_id="agent-a",
                            agent_name="a",
                            task="do something",
                            response_text="answer",
                            success=True,
                        )
                    ],
                    started_at=datetime.now(UTC),
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

    frames = await _drain_sse(conn)
    completed_frames = [
        (kind, data)
        for kind, data in frames
        if kind == "processing_status" and data.get("status") == "completed"
    ]
    assert len(completed_frames) == 1
    assert completed_frames[0][1]["details"]["turn_completion_kind"] == "synthesis"
    assert completed_frames[0][1]["details"]["turn_phase"] == "terminal"
