import asyncio
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from common.dto import (
    ExecutionAck,
    ExecutionRequest,
    HubAgentResponseInternal,
    ProcessingStatusEvent,
    RunInfo,
)
from common.utils.time import utcnow
from execution.facade import (
    ExecutionFacade,
    hub_agent_response_internal_to_agent_event,
)
from execution.orchestration.run_store import (
    InMemoryOrchestrationRunStore,
    OrchestrationStoreConflict,
)
from execution.translators import room_response_to_execution_ack
from models.orchestration import (
    OrchestrationRunState,
    OrchestrationStatus,
    PendingAgentContinuation,
)
from models.response import RoomCenterUserMessageResponse


class RecordingTaskFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, coro, *, name=None):
        self.calls.append(name)
        return asyncio.create_task(coro, name=name)


def _make_facade(**overrides):
    room_center = SimpleNamespace(send_message_to_room=AsyncMock())

    async def persist_message_to_room(*args, **kwargs):
        response = await room_center.send_message_to_room(*args, **kwargs)
        return response, response if response.message_id else None

    async def run_message_preflight_to_room(context):
        return context

    room_center.persist_message_to_room = AsyncMock(side_effect=persist_message_to_room)
    room_center.run_message_preflight_to_room = AsyncMock(
        side_effect=run_message_preflight_to_room
    )
    room_message_center = SimpleNamespace(process_room_user_message=AsyncMock())
    hitl_manager = SimpleNamespace(
        request_input=AsyncMock(),
        handle_response=AsyncMock(),
        get_pending_requests=AsyncMock(return_value=[]),
        cancel_request=AsyncMock(return_value=None),
    )
    run_lifecycle = SimpleNamespace(
        heal_diverged_runs=AsyncMock(return_value=2),
        record_processing_status=AsyncMock(return_value=None),
    )
    run_reader = SimpleNamespace(
        get_run=AsyncMock(return_value=None),
        get_runs_for_room=AsyncMock(return_value=[]),
    )
    cancellation_state = SimpleNamespace(
        cancel_message_and_broadcast=AsyncMock(),
        clear_cancellation=MagicMock(),
    )
    cancellation_store = SimpleNamespace(cancel_message=AsyncMock(return_value=True))
    hitl_message_cancellation = SimpleNamespace(
        cancel_requests_for_message=AsyncMock(),
    )
    agent_task_cleanup = SimpleNamespace(
        cleanup_cancelled_message_tasks=AsyncMock(),
    )
    agent_response_handler = SimpleNamespace(handle=AsyncMock())
    event_publisher = SimpleNamespace(emit=AsyncMock())
    client_request_id_resolver = SimpleNamespace(
        resolve_client_request_id=AsyncMock(side_effect=lambda _, provided: provided),
    )
    deps = {
        "room_center": room_center,
        "room_message_center": room_message_center,
        "hitl_manager": hitl_manager,
        "run_lifecycle": run_lifecycle,
        "run_reader": run_reader,
        "cancellation_state": cancellation_state,
        "cancellation_store": cancellation_store,
        "hitl_message_cancellation": hitl_message_cancellation,
        "agent_task_cleanup": agent_task_cleanup,
        "agent_response_handler": agent_response_handler,
        "event_publisher": event_publisher,
        "run_event_enabled": lambda: False,
        "client_request_id_resolver": client_request_id_resolver,
        "task_factory": RecordingTaskFactory(),
    }
    deps.update(overrides)
    facade = ExecutionFacade(**deps)
    return facade, deps


def _assert_processing_status_event(
    event,
    *,
    room_id: str,
    message_id: str,
    status: str,
    related_message_id: str,
    client_request_id: str,
    details: dict | None,
):
    assert isinstance(event, ProcessingStatusEvent)
    assert event.event_type == "processing_status"
    assert event.room_id == room_id
    assert event.message_id == message_id
    assert event.related_message_id == related_message_id
    assert event.status == status
    assert event.client_request_id == client_request_id
    assert event.details == details
    assert event.timestamp is None
    assert event.trace_id is None
    assert event.agent_id is None
    assert event.agents is None


def _room_response_with_preflight(**kwargs) -> RoomCenterUserMessageResponse:
    assert "preflight_outcome" in RoomCenterUserMessageResponse.model_fields
    assert "preflight_details" in RoomCenterUserMessageResponse.model_fields
    response = RoomCenterUserMessageResponse(**kwargs)
    assert response.preflight_outcome == kwargs.get("preflight_outcome")
    assert response.preflight_details == kwargs.get("preflight_details")
    dumped = response.model_dump(mode="json")
    assert dumped["preflight_outcome"] == kwargs.get("preflight_outcome")
    assert dumped["preflight_details"] == kwargs.get("preflight_details")
    return response


def test_constructor_core_dependencies_are_typed_ports():
    hints = get_type_hints(ExecutionFacade.__init__)

    assert hints["room_center"].__name__ == "RoomCenterPort"
    assert hints["room_message_center"].__name__ == "RoomMessageCenterPort"
    assert hints["hitl_manager"].__name__ == "HITLServicePort"


@pytest.mark.asyncio
async def test_execute_persists_ack_without_starting_orchestration():
    facade, deps = _make_facade()
    deps["room_center"].send_message_to_room.return_value = RoomCenterUserMessageResponse(
        room_id="room-1",
        message_id="msg-1",
        user_id="user-1",
        user_name="User",
        success=True,
    )
    request = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        sender_name="User",
        message={
            "room_id": "room-1",
            "message_id": "draft-msg-1",
            "message_type": "user",
            "message_content": {"message_text": "hello"},
        },
        target_group=None,
        mentioned_agent_ids=["agent-1"],
    )

    ack = await facade.execute(request)

    assert ack.message_id == "msg-1"
    deps["room_center"].send_message_to_room.assert_awaited_once()
    sent_request = deps["room_center"].send_message_to_room.await_args.args[0]
    assert sent_request.user_id == "user-1"
    assert sent_request.message.message_content.message_text == "hello"
    assert deps["room_center"].send_message_to_room.await_args.args[1:] == (
        None,
        ["agent-1"],
    )
    deps["room_message_center"].process_room_user_message.assert_not_called()


@pytest.mark.asyncio
async def test_execute_rejects_pending_hitl_before_room_persist():
    facade, deps = _make_facade()
    deps["hitl_manager"].get_pending_requests.return_value = [SimpleNamespace()]
    deps["room_center"].send_message_to_room.side_effect = AssertionError(
        "room persist should not be called"
    )

    ack = await facade.execute(
        ExecutionRequest(room_id="room-1", sender_id="user-1", client_request_id="cr-1")
    )

    assert ack.success is False
    assert ack.room_id == "room-1"
    assert ack.status_code == 409
    assert ack.should_start_orchestration is False
    assert "waiting for your input" in ack.error
    deps["hitl_manager"].get_pending_requests.assert_awaited_once_with("room-1")
    deps["room_center"].send_message_to_room.assert_not_awaited()
    deps["run_reader"].get_run.assert_not_awaited()
    deps["run_reader"].get_runs_for_room.assert_not_awaited()
    deps["run_lifecycle"].record_processing_status.assert_not_awaited()
    deps["event_publisher"].emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_rejects_active_run_before_room_persist():
    facade, deps = _make_facade()
    deps["room_center"].send_message_to_room.side_effect = AssertionError(
        "room persist should not be called"
    )
    deps["run_reader"].get_runs_for_room.return_value = [
        RunInfo(
            run_id="run-1",
            room_id="room-1",
            state="processing",
            trigger_message_id="msg-active",
        )
    ]

    ack = await facade.execute(
        ExecutionRequest(room_id="room-1", sender_id="user-1", client_request_id="cr-1")
    )

    assert ack.success is False
    assert ack.room_id == "room-1"
    assert ack.status_code == 409
    assert ack.should_start_orchestration is False
    assert "already processing" in ack.error
    deps["hitl_manager"].get_pending_requests.assert_awaited_once_with("room-1")
    deps["run_reader"].get_runs_for_room.assert_awaited_once_with("room-1")
    deps["room_center"].send_message_to_room.assert_not_awaited()
    deps["run_lifecycle"].record_processing_status.assert_not_awaited()
    deps["event_publisher"].emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_continues_when_hitl_pending_lookup_fails():
    facade, deps = _make_facade()
    order: list[str] = []

    async def get_pending_requests(_room_id):
        order.append("hitl")
        raise RuntimeError("hitl down")

    async def get_runs_for_room(_room_id):
        order.append("active_runs")
        return []

    async def send_message_to_room(*_args, **_kwargs):
        order.append("persist")
        return _room_response_with_preflight(
            room_id="room-1",
            message_id="msg-1",
            dispatch_root_message_id="msg-1",
            success=True,
            preflight_outcome="ready",
        )

    deps["hitl_manager"].get_pending_requests.side_effect = get_pending_requests
    deps["run_reader"].get_runs_for_room.side_effect = get_runs_for_room
    deps["room_center"].send_message_to_room.side_effect = send_message_to_room

    ack = await facade.execute(
        ExecutionRequest(room_id="room-1", sender_id="user-1", client_request_id="cr-1")
    )

    assert ack.success is True
    assert ack.message_id == "msg-1"
    assert ack.should_start_orchestration is True
    assert order == ["hitl", "active_runs", "persist"]
    deps["hitl_manager"].get_pending_requests.assert_awaited_once_with("room-1")
    deps["room_center"].send_message_to_room.assert_awaited_once()
    deps["run_reader"].get_runs_for_room.assert_awaited_once_with("room-1")


@pytest.mark.asyncio
async def test_execute_continues_when_active_run_lookup_fails():
    facade, deps = _make_facade()
    order: list[str] = []

    async def get_pending_requests(_room_id):
        order.append("hitl")
        return []

    async def get_runs_for_room(_room_id):
        order.append("active_runs")
        raise RuntimeError("runs down")

    async def send_message_to_room(*_args, **_kwargs):
        order.append("persist")
        return _room_response_with_preflight(
            room_id="room-1",
            message_id="msg-1",
            dispatch_root_message_id="msg-1",
            success=True,
            preflight_outcome="ready",
        )

    deps["hitl_manager"].get_pending_requests.side_effect = get_pending_requests
    deps["run_reader"].get_runs_for_room.side_effect = get_runs_for_room
    deps["room_center"].send_message_to_room.side_effect = send_message_to_room

    ack = await facade.execute(
        ExecutionRequest(room_id="room-1", sender_id="user-1", client_request_id="cr-1")
    )

    assert ack.success is True
    assert ack.message_id == "msg-1"
    assert ack.should_start_orchestration is True
    assert order == ["hitl", "active_runs", "persist"]
    deps["hitl_manager"].get_pending_requests.assert_awaited_once_with("room-1")
    deps["run_reader"].get_runs_for_room.assert_awaited_once_with("room-1")
    deps["room_center"].send_message_to_room.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_emits_processing_for_ready_room_preflight():
    facade, deps = _make_facade()
    deps["room_center"].send_message_to_room.return_value = _room_response_with_preflight(
        room_id="room-1",
        message_id="frontend-msg-1",
        dispatch_root_message_id="root-msg-1",
        success=True,
        preflight_outcome="ready",
    )
    order: list[tuple[str, str]] = []

    async def record_status(_room_id, status, _message_id, **_kwargs):
        order.append(("record", status))

    async def emit_event(event):
        order.append(("emit", event.status))

    deps["run_lifecycle"].record_processing_status.side_effect = record_status
    deps["event_publisher"].emit.side_effect = emit_event

    ack = await facade.execute(
        ExecutionRequest(room_id="room-1", sender_id="user-1", client_request_id="cr-1")
    )

    assert ack.should_start_orchestration is True
    assert order == [("record", "processing"), ("emit", "processing")]
    deps["run_lifecycle"].record_processing_status.assert_awaited_once_with(
        "room-1",
        "processing",
        "root-msg-1",
        client_request_id="cr-1",
        details=None,
        error_message=None,
    )
    deps["event_publisher"].emit.assert_awaited_once()
    event = deps["event_publisher"].emit.await_args.args[0]
    _assert_processing_status_event(
        event,
        room_id="room-1",
        message_id="frontend-msg-1",
        status="processing",
        related_message_id="root-msg-1",
        client_request_id="cr-1",
        details=None,
    )


@pytest.mark.asyncio
async def test_execute_emits_processing_before_room_preflight_continuation():
    preflight_context = object()
    room_center = SimpleNamespace(
        send_message_to_room=AsyncMock(
            side_effect=AssertionError("legacy single-step room path should not be used")
        ),
        persist_message_to_room=AsyncMock(
            return_value=(
                RoomCenterUserMessageResponse(
                    room_id="room-1",
                    message_id="msg-1",
                    dispatch_root_message_id="msg-1",
                    success=True,
                ),
                preflight_context,
            )
        ),
        run_message_preflight_to_room=AsyncMock(),
    )
    facade, deps = _make_facade(room_center=room_center)
    order: list[str] = []

    async def record_status(_room_id, status, _message_id, **_kwargs):
        order.append(f"record:{status}")

    async def emit_event(event):
        order.append(f"emit:{event.status}")

    async def continue_preflight(context):
        assert context is preflight_context
        order.append("room_preflight")
        return _room_response_with_preflight(
            room_id="room-1",
            message_id="msg-1",
            dispatch_root_message_id="msg-1",
            success=True,
            preflight_outcome="ready",
        )

    deps["run_lifecycle"].record_processing_status.side_effect = record_status
    deps["event_publisher"].emit.side_effect = emit_event
    room_center.run_message_preflight_to_room.side_effect = continue_preflight

    ack = await facade.execute(
        ExecutionRequest(room_id="room-1", sender_id="user-1", client_request_id="cr-1")
    )

    assert ack.success is True
    assert ack.should_start_orchestration is True
    assert order == ["record:processing", "emit:processing", "room_preflight"]
    room_center.persist_message_to_room.assert_awaited_once()
    room_center.run_message_preflight_to_room.assert_awaited_once_with(preflight_context)
    room_center.send_message_to_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_does_not_emit_completed_for_success_without_preflight_outcome():
    facade, deps = _make_facade()
    deps["room_center"].send_message_to_room.return_value = RoomCenterUserMessageResponse(
        room_id="room-1",
        message_id="msg-1",
        success=True,
        status_code=200,
    )

    ack = await facade.execute(
        ExecutionRequest(room_id="room-1", sender_id="user-1", client_request_id="cr-1")
    )

    assert ack.success is True
    assert ack.should_start_orchestration is False
    deps["run_lifecycle"].record_processing_status.assert_awaited_once_with(
        "room-1",
        "processing",
        "msg-1",
        client_request_id="cr-1",
        details=None,
        error_message=None,
    )
    event = deps["event_publisher"].emit.await_args.args[0]
    assert event.status == "processing"


@pytest.mark.asyncio
async def test_execute_emits_completed_for_completed_room_preflight():
    facade, deps = _make_facade()
    deps["room_center"].send_message_to_room.return_value = _room_response_with_preflight(
        room_id="room-1",
        message_id="msg-1",
        success=True,
        status_code=200,
        preflight_outcome="completed",
    )

    ack = await facade.execute(
        ExecutionRequest(room_id="room-1", sender_id="user-1", client_request_id="cr-1")
    )

    assert ack.success is True
    assert ack.should_start_orchestration is False
    assert [
        await_call.args[1]
        for await_call in deps["run_lifecycle"].record_processing_status.await_args_list
    ] == [
        "processing",
        "completed",
    ]
    assert [
        await_call.args[0].status
        for await_call in deps["event_publisher"].emit.await_args_list
    ] == [
        "processing",
        "completed",
    ]


@pytest.mark.asyncio
async def test_execute_returns_ack_when_post_persist_status_emit_fails():
    facade, deps = _make_facade()
    deps["room_center"].send_message_to_room.return_value = _room_response_with_preflight(
        room_id="room-1",
        message_id="msg-1",
        dispatch_root_message_id="msg-1",
        success=True,
        preflight_outcome="ready",
    )
    deps["run_lifecycle"].record_processing_status.side_effect = RuntimeError("sse down")

    ack = await facade.execute(
        ExecutionRequest(room_id="room-1", sender_id="user-1", client_request_id="cr-1")
    )

    assert ack.message_id == "msg-1"
    assert ack.success is True
    assert ack.should_start_orchestration is True
    deps["room_center"].send_message_to_room.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_emits_processing_then_failed_for_persisted_preflight_failure():
    facade, deps = _make_facade()
    deps["room_center"].send_message_to_room.return_value = _room_response_with_preflight(
        room_id="room-1",
        message_id="msg-1",
        success=False,
        error="Failed to parse user message",
        status_code=500,
        preflight_outcome="failed",
        preflight_details="Failed to parse user message",
    )
    order: list[tuple[str, str]] = []

    async def record_status(_room_id, status, _message_id, **_kwargs):
        order.append(("record", status))

    async def emit_event(event):
        order.append(("emit", event.status))

    deps["run_lifecycle"].record_processing_status.side_effect = record_status
    deps["event_publisher"].emit.side_effect = emit_event

    ack = await facade.execute(
        ExecutionRequest(room_id="room-1", sender_id="user-1", client_request_id="cr-1")
    )

    assert ack.success is False
    assert ack.should_start_orchestration is False
    assert ack.error == "Failed to parse user message"
    assert ack.status_code == 500
    assert order == [
        ("record", "processing"),
        ("emit", "processing"),
        ("record", "failed"),
        ("emit", "failed"),
    ]
    assert deps["run_lifecycle"].record_processing_status.await_args_list == [
        call(
            "room-1",
            "processing",
            "msg-1",
            client_request_id="cr-1",
            details=None,
            error_message=None,
        ),
        call(
            "room-1",
            "failed",
            "msg-1",
            client_request_id="cr-1",
            details={"message": "Failed to parse user message"},
            error_message="Failed to parse user message",
        ),
    ]
    events = [
        await_call.args[0]
        for await_call in deps["event_publisher"].emit.await_args_list
    ]
    assert len(events) == 2
    _assert_processing_status_event(
        events[0],
        room_id="room-1",
        message_id="msg-1",
        status="processing",
        related_message_id="msg-1",
        client_request_id="cr-1",
        details=None,
    )
    _assert_processing_status_event(
        events[1],
        room_id="room-1",
        message_id="msg-1",
        status="failed",
        related_message_id="msg-1",
        client_request_id="cr-1",
        details={"message": "Failed to parse user message"},
    )


@pytest.mark.asyncio
async def test_execute_emits_canceled_for_canceled_room_preflight():
    facade, deps = _make_facade()
    deps["room_center"].send_message_to_room.return_value = _room_response_with_preflight(
        room_id="room-1",
        message_id="msg-1",
        success=True,
        status_code=200,
        preflight_outcome="canceled",
    )
    order: list[tuple[str, str]] = []

    async def record_status(_room_id, status, _message_id, **_kwargs):
        order.append(("record", status))

    async def emit_event(event):
        order.append(("emit", event.status))

    deps["run_lifecycle"].record_processing_status.side_effect = record_status
    deps["event_publisher"].emit.side_effect = emit_event

    ack = await facade.execute(
        ExecutionRequest(room_id="room-1", sender_id="user-1", client_request_id="cr-1")
    )

    assert ack.success is True
    assert ack.should_start_orchestration is False
    assert order == [
        ("record", "processing"),
        ("emit", "processing"),
        ("record", "canceled"),
        ("emit", "canceled"),
    ]
    assert deps["run_lifecycle"].record_processing_status.await_args_list == [
        call(
            "room-1",
            "processing",
            "msg-1",
            client_request_id="cr-1",
            details=None,
            error_message=None,
        ),
        call(
            "room-1",
            "canceled",
            "msg-1",
            client_request_id="cr-1",
            details=None,
            error_message=None,
        ),
    ]
    events = [
        await_call.args[0]
        for await_call in deps["event_publisher"].emit.await_args_list
    ]
    assert len(events) == 2
    _assert_processing_status_event(
        events[0],
        room_id="room-1",
        message_id="msg-1",
        status="processing",
        related_message_id="msg-1",
        client_request_id="cr-1",
        details=None,
    )
    _assert_processing_status_event(
        events[1],
        room_id="room-1",
        message_id="msg-1",
        status="canceled",
        related_message_id="msg-1",
        client_request_id="cr-1",
        details=None,
    )


@pytest.mark.asyncio
async def test_start_orchestration_tracks_and_awaits_background_task():
    task_factory = RecordingTaskFactory()
    facade, deps = _make_facade(task_factory=task_factory)
    request = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        client_request_id="cr-1",
        parent_message_id="parent-1",
    )
    ack = ExecutionAck(success=True, message_id="msg-1")

    await facade.start_orchestration(request, ack)

    orchestration_request = deps["room_message_center"].process_room_user_message.call_args.args[0]
    assert orchestration_request.room_id == "room-1"
    assert orchestration_request.room_user_message_id == "msg-1"
    assert orchestration_request.user_id == "user-1"
    assert orchestration_request.client_request_id == "cr-1"
    assert orchestration_request.room_related_message_id == "parent-1"
    assert task_factory.calls == ["execution-orchestrate-msg-1"]
    assert facade._inflight == set()


@pytest.mark.asyncio
async def test_start_orchestration_skips_when_ack_disables_dispatch():
    facade, deps = _make_facade()
    request = ExecutionRequest(room_id="room-1", sender_id="user-1")
    ack = ExecutionAck(
        success=True,
        message_id="msg-1",
        should_start_orchestration=False,
    )

    await facade.start_orchestration(request, ack)

    deps["room_message_center"].process_room_user_message.assert_not_called()
    assert facade._inflight == set()


@pytest.mark.asyncio
async def test_resolve_hitl_updates_orchestration_state_after_successful_response():
    hitl_manager = MagicMock()
    hitl_manager.handle_response = AsyncMock(
        return_value={
            "request_id": "hitl-1",
            "status": "resolved",
            "room_id": "room-1",
            "user_message_id": "msg-1",
            "orchestration_run_id": "run-1",
            "source": "supervisor",
            "response": "annual revenue is $2M",
        }
    )
    hitl_manager.request_input = AsyncMock()
    hitl_manager.get_pending_requests = AsyncMock(return_value=[])
    hitl_manager.cancel_request = AsyncMock(return_value=None)
    run_store = InMemoryOrchestrationRunStore()
    await run_store.create_run(
        OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Get quote",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.AWAITING_USER,
            pending_hitl_request_ids=["hitl-1"],
            open_questions=[{"request_id": "hitl-1", "status": "open"}],
        )
    )
    facade, deps = _make_facade(
        hitl_manager=hitl_manager,
        orchestration_run_store=run_store,
    )
    recovery_started = asyncio.Event()
    release_recovery = asyncio.Event()

    async def process_recovery(_request):
        recovery_started.set()
        await release_recovery.wait()

    deps["room_message_center"].process_room_user_message.side_effect = (
        process_recovery
    )

    result = await asyncio.wait_for(
        facade.resolve_hitl(
            room_id="room-1",
            request_id="hitl-1",
            response="annual revenue is $2M",
            responder_id="user-1",
        ),
        timeout=1.0,
    )
    await asyncio.wait_for(recovery_started.wait(), timeout=1.0)

    saved = await run_store.get_run("run-1")
    assert result.request_id == "hitl-1"
    assert saved is not None
    assert "hitl-1" not in saved.pending_hitl_request_ids
    assert saved.open_questions[-1]["status"] == "resolved"
    assert saved.open_questions[-1]["answer"] == "annual revenue is $2M"
    assert saved.status == OrchestrationStatus.RUNNING
    assert len(facade._inflight) == 1
    deps["room_message_center"].process_room_user_message.assert_awaited_once()
    resumed_request = deps["room_message_center"].process_room_user_message.await_args.args[0]
    assert resumed_request.room_id == "room-1"
    assert resumed_request.room_user_message_id == "msg-1"
    assert resumed_request.is_recovery is True
    release_recovery.set()
    await asyncio.gather(*facade._inflight)


@pytest.mark.asyncio
async def test_resolve_hitl_raises_after_repeated_run_store_conflicts():
    hitl_manager = MagicMock()
    hitl_manager.handle_response = AsyncMock(
        return_value={
            "request_id": "hitl-1",
            "status": "resolved",
            "room_id": "room-1",
            "user_message_id": "msg-1",
            "orchestration_run_id": "run-1",
            "source": "supervisor",
            "response": "annual revenue is $2M",
        }
    )
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Get quote",
        candidate_agent_ids=["agent-1"],
        status=OrchestrationStatus.AWAITING_USER,
        pending_hitl_request_ids=["hitl-1"],
        open_questions=[{"request_id": "hitl-1", "status": "open"}],
    )
    run_store = MagicMock()
    run_store.get_run = AsyncMock(return_value=state)
    run_store.save_state = AsyncMock(
        side_effect=OrchestrationStoreConflict("concurrent update")
    )
    run_store.append_event = AsyncMock()
    facade, deps = _make_facade(
        hitl_manager=hitl_manager,
        orchestration_run_store=run_store,
    )

    with pytest.raises(OrchestrationStoreConflict, match="failed to record resolved HITL"):
        await facade.resolve_hitl(
            room_id="room-1",
            request_id="hitl-1",
            response="annual revenue is $2M",
            responder_id="user-1",
        )

    assert run_store.save_state.await_count == 2
    run_store.append_event.assert_not_awaited()
    deps["room_message_center"].process_room_user_message.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_hitl_records_policy_followup_without_queue_resume():
    hitl_manager = MagicMock()
    hitl_manager.handle_response = AsyncMock(
        return_value={
            "request_id": "hitl-1",
            "status": "resolved",
            "room_id": "room-1",
            "user_message_id": "msg-1",
            "orchestration_run_id": "run-1",
            "source": "agent",
            "response": "I approve the policy exception.",
            "followup_hitl_request_id": "hitl-2",
            "followup_prompt": "Please approve the required policy.",
            "task_state": "policy-required",
            "agent_id": "agent-1",
            "agent_name": "Agent One",
            "display_message_id": "agent-msg-1",
            "continuation_message_id": "agent-msg-1",
            "a2a_task_id": "task-1",
            "a2a_context_id": "ctx-1",
        }
    )
    hitl_manager.request_input = AsyncMock()
    hitl_manager.get_pending_requests = AsyncMock(return_value=[])
    hitl_manager.cancel_request = AsyncMock(return_value=None)
    run_store = InMemoryOrchestrationRunStore()
    await run_store.create_run(
        OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Get quote",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.AWAITING_USER,
            pending_hitl_request_ids=["hitl-1"],
            open_questions=[
                {
                    "request_id": "hitl-1",
                    "source": "agent",
                    "status": "open",
                    "a2a_task_id": "task-1",
                    "a2a_context_id": "ctx-1",
                }
            ],
            pending_agent_continuations=[
                PendingAgentContinuation(
                    continuation_id="cont-1",
                    source_intent_id="intent-1",
                    source_agent_message_id="agent-msg-1",
                    agent_id="agent-1",
                    goal_family_fingerprint="family-1",
                    goal_revision_fingerprint="revision-1",
                    a2a_task_id="task-1",
                    a2a_context_id="ctx-1",
                )
            ],
        )
    )
    facade, deps = _make_facade(
        hitl_manager=hitl_manager,
        orchestration_run_store=run_store,
    )

    await facade.resolve_hitl(
        room_id="room-1",
        request_id="hitl-1",
        response="I approve the policy exception.",
        responder_id="user-1",
    )

    saved = await run_store.get_run("run-1")
    assert saved is not None
    assert saved.status == OrchestrationStatus.AWAITING_USER
    assert saved.pending_hitl_request_ids == ["hitl-2"]
    old_question = next(
        question for question in saved.open_questions
        if question.get("request_id") == "hitl-1"
    )
    next_question = next(
        question for question in saved.open_questions
        if question.get("request_id") == "hitl-2"
    )
    assert old_question["status"] == "resolved"
    assert old_question["answer"] == "I approve the policy exception."
    assert next_question["status"] == "open"
    assert next_question["source"] == "agent"
    assert next_question["a2a_task_id"] == "task-1"
    assert next_question["a2a_context_id"] == "ctx-1"
    assert saved.pending_agent_continuations[-1].a2a_task_id == "task-1"
    assert saved.pending_agent_continuations[-1].a2a_context_id == "ctx-1"
    deps["room_message_center"].process_room_user_message.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_preserves_order_and_requested_by_user_id():
    order = []
    facade, deps = _make_facade()

    async def broadcast(message_id):
        order.append("broadcast")

    async def cancel_hitl(message_id):
        order.append("hitl")

    async def persist(message_id, user_id):
        order.append(("persist", user_id))
        return True

    async def record(*args, **kwargs):
        order.append("record")

    async def cleanup(**kwargs):
        order.append("cleanup")

    deps["cancellation_state"].cancel_message_and_broadcast.side_effect = broadcast
    deps["hitl_message_cancellation"].cancel_requests_for_message.side_effect = cancel_hitl
    deps["cancellation_store"].cancel_message.side_effect = persist
    deps["run_lifecycle"].record_processing_status.side_effect = record
    deps["agent_task_cleanup"].cleanup_cancelled_message_tasks.side_effect = cleanup

    assert await facade.cancel(
        "room-1",
        "msg-1",
        requested_by_user_id="user-1",
    )

    assert order == [("persist", "user-1"), "broadcast", "hitl", "record", "cleanup"]
    deps["cancellation_state"].clear_cancellation.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_terminalizes_awaiting_orchestration_and_clears_hitl_state():
    run_store = InMemoryOrchestrationRunStore()
    await run_store.create_run(
        OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Get quote",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.AWAITING_USER,
            pending_hitl_request_ids=["hitl-1"],
            open_questions=[{"request_id": "hitl-1", "status": "open"}],
        )
    )
    facade, _ = _make_facade(orchestration_run_store=run_store)

    assert await facade.cancel(
        "room-1",
        "msg-1",
        requested_by_user_id="user-1",
    )

    saved = await run_store.get_run("run-1")
    assert saved is not None
    assert saved.status == OrchestrationStatus.CANCELED
    assert saved.pending_hitl_request_ids == []
    assert saved.open_questions == [
        {"request_id": "hitl-1", "status": "canceled"}
    ]


@pytest.mark.asyncio
async def test_cancel_does_not_rewrite_budget_exhausted_orchestration():
    run_store = InMemoryOrchestrationRunStore()
    await run_store.create_run(
        OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Get quote",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.BUDGET_EXHAUSTED,
            terminal_reason="step budget exhausted",
        )
    )
    facade, deps = _make_facade(orchestration_run_store=run_store)

    assert await facade.cancel(
        "room-1",
        "msg-1",
        requested_by_user_id="user-1",
    )

    saved = await run_store.get_run("run-1")
    assert saved is not None
    assert saved.status == OrchestrationStatus.BUDGET_EXHAUSTED
    assert saved.terminal_reason == "step budget exhausted"
    deps["cancellation_store"].cancel_message.assert_not_awaited()
    deps["cancellation_state"].cancel_message_and_broadcast.assert_not_awaited()
    deps["agent_task_cleanup"].cleanup_cancelled_message_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_clears_cancellation_when_persistence_fails():
    facade, deps = _make_facade()
    deps["cancellation_store"].cancel_message.return_value = False

    assert not await facade.cancel(
        "room-1",
        "msg-1",
        requested_by_user_id="user-1",
    )

    deps["cancellation_state"].cancel_message_and_broadcast.assert_not_awaited()
    deps["hitl_message_cancellation"].cancel_requests_for_message.assert_not_awaited()
    deps["cancellation_state"].clear_cancellation.assert_not_called()
    deps["run_lifecycle"].record_processing_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_methods_delegate_to_ports():
    facade, deps = _make_facade()
    deps["run_reader"].get_runs_for_room.return_value = [
        SimpleNamespace(trigger_message_id="user-msg-1")
    ]

    assert await facade.get_run("run-1") is None
    runs = await facade.get_runs_for_room("room-1")
    assert runs[0].trigger_message_id == "user-msg-1"
    assert await facade.heal_diverged_runs(limit=123) == 2
    deps["run_lifecycle"].heal_diverged_runs.assert_awaited_once_with(limit=123)


@pytest.mark.asyncio
async def test_cancel_inflight_tasks_awaits_cancelled_tasks():
    async def wait_forever():
        try:
            await asyncio.Event().wait()
        finally:
            marker.append("cleanup")

    marker = []
    facade, deps = _make_facade()
    task = facade._spawn_orchestration(
        wait_forever(),
        name="execution-test",
        room_id="room-1",
        message_id="msg-1",
        client_request_id="cr-1",
    )
    await asyncio.sleep(0)

    assert await facade.cancel_inflight_tasks() == 1
    assert task.cancelled()
    assert marker == ["cleanup"]
    assert facade._inflight == set()
    assert facade._inflight_metadata == {}
    deps["run_lifecycle"].record_processing_status.assert_awaited_once_with(
        "room-1",
        "canceled",
        "msg-1",
        client_request_id="cr-1",
        details=None,
        error_message=None,
    )


@pytest.mark.asyncio
async def test_cancel_inflight_tasks_does_not_mark_task_that_completes_during_shutdown():
    async def completes_normally():
        return "done"

    facade, deps = _make_facade()
    task = asyncio.create_task(completes_normally(), name="execution-test")
    await task
    facade._inflight.add(task)
    facade._inflight_metadata[task] = {
        "room_id": "room-1",
        "message_id": "msg-1",
        "client_request_id": "cr-1",
    }

    assert await facade.cancel_inflight_tasks() == 0
    assert task.done()
    assert not task.cancelled()
    deps["run_lifecycle"].record_processing_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_hitl_methods_delegate_and_translate():
    facade, deps = _make_facade()
    model_request = SimpleNamespace(
        request_id="req-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        source="agent",
        prompt="Need input",
        prompt_type="text",
        status="pending",
        display_message_id="display-msg-1",
    )
    deps["hitl_manager"].request_input.return_value = model_request
    deps["hitl_manager"].handle_response.return_value = {
        "status": "ok",
        "request_id": "req-1",
    }
    deps["hitl_manager"].get_pending_requests.return_value = [model_request]

    created = await facade.create_hitl_request(
        "room-1",
        "user-msg-1",
        "Need input",
        "agent",
        display_message_id="display-msg-1",
    )
    resolved = await facade.resolve_hitl("room-1", "req-1", "yes", "user-1")
    pending = await facade.get_pending_hitl("room-1")
    canceled = await facade.cancel_hitl("room-1", "req-1")

    assert created.message_id == "display-msg-1"
    assert resolved.status == "ok"
    assert pending[0].message_id == "display-msg-1"
    assert canceled is True
    deps["hitl_manager"].cancel_request.assert_awaited_once_with(
        "req-1",
        room_id="room-1",
    )


def test_room_response_to_execution_ack_preserves_missing_message_error_shape():
    response = RoomCenterUserMessageResponse(
        success=False,
        error="Message is required",
        status_code=400,
        message_id=None,
        message=None,
    )

    ack = room_response_to_execution_ack(response)

    assert ack.message_id is None
    assert ack.message is None
    assert ack.error == "Message is required"


def test_room_response_to_execution_ack_skips_orchestration_without_dispatch_root():
    response = RoomCenterUserMessageResponse(
        success=True,
        message_id="msg-1",
        dispatch_root_message_id=None,
    )

    ack = room_response_to_execution_ack(response)

    assert ack.success is True
    assert ack.message_id == "msg-1"
    assert ack.should_start_orchestration is False


@pytest.mark.asyncio
async def test_handle_hub_agent_response_delegates_to_agent_response_handler():
    facade, deps = _make_facade()
    event = HubAgentResponseInternal(
        hub_id="hub-1",
        agent_id="agent-1",
        task_id="task-1",
        room_id="room-1",
        is_terminal=True,
        timestamp=utcnow(),
        payload={
            "kind": "response",
            "message_id": "agent-msg-1",
            "text": "done",
            "parts": [{"kind": "text", "text": "done"}],
            "is_final": True,
        },
    )

    await facade.handle_hub_agent_response(event)

    deps["agent_response_handler"].handle.assert_awaited_once()
    agent_event = deps["agent_response_handler"].handle.await_args.args[0]
    assert agent_event.kind == "response"
    assert agent_event.room_id == "room-1"
    assert agent_event.message_id == "agent-msg-1"
    assert agent_event.agent_id == "agent-1"
    assert agent_event.task_id == "task-1"
    assert agent_event.text == "done"
    assert agent_event.parts == [{"kind": "text", "text": "done"}]


def test_hub_agent_response_adapter_maps_legacy_final_event_type():
    event = HubAgentResponseInternal(
        hub_id="hub-1",
        agent_id="agent-1",
        task_id="task-1",
        room_id="room-1",
        is_terminal=True,
        timestamp=utcnow(),
        payload={"event_type": "final", "message_id": "msg-1", "text": "done"},
    )

    agent_event = hub_agent_response_internal_to_agent_event(event)

    assert agent_event.kind == "response"
    assert agent_event.is_final is True


def test_hub_agent_response_adapter_maps_continuation_message_id_and_thaws_payload():
    event = HubAgentResponseInternal(
        hub_id="hub-1",
        agent_id="agent-1",
        task_id="task-1",
        room_id="room-1",
        is_terminal=True,
        timestamp=utcnow(),
        payload={
            "kind": "response",
            "continuation_message_id": "continuation-1",
            "artifacts": [{"parts": [{"file": {"metadata": {"x": 1}}}]}],
        },
    )

    agent_event = hub_agent_response_internal_to_agent_event(event)
    agent_event.artifacts[0]["parts"][0]["file"]["metadata"]["x"] = 2

    assert agent_event.message_id == "continuation-1"
    assert agent_event.artifacts[0]["parts"][0]["file"]["metadata"]["x"] == 2


@pytest.mark.parametrize(
    "payload,is_terminal,error",
    [
        ({"kind": "partial", "message_id": "m1"}, False, "Unsupported non-terminal"),
        ({"kind": "unknown", "message_id": "m1"}, False, "Unsupported AgentEvent"),
        ({"kind": "response", "message_id": "m1"}, False, "requires terminal"),
        ({"kind": "response", "message_id": "m1", "is_final": False}, True, "is_final=False"),
        ({"kind": "processing_status", "message_id": "m1"}, False, "requires state"),
        (
            {"kind": "processing_status", "message_id": "m1", "state": "working"},
            False,
            "Unsupported Hub AgentEvent state",
        ),
        (
            {
                "kind": "processing_status",
                "message_id": "m1",
                "state": "processing",
                "lifecycle_message_id": "root-1",
            },
            False,
            "requires upstream",
        ),
        (
            {"kind": "processing_status", "message_id": "m1", "state": "processing"},
            False,
            "requires verified lifecycle_message_id",
        ),
        ({"kind": "error", "message_id": "m1"}, True, "requires error_text or text"),
        ({"kind": "interactive", "message_id": "m1", "state": "working"}, False, "Unsupported"),
        ({"kind": "response", "message_id": 123}, True, "non-empty string"),
        ({"kind": "response", "message_id": "m1", "task_id": "other"}, True, "conflicts"),
        ({"kind": "response", "message_id": "m1", "parts": ["bad"]}, True, "list of objects"),
        ({"kind": "response", "message_id": "m1", "append": "yes"}, True, "must be a boolean"),
        ({"kind": "response", "message_id": "m1", "step_number": True}, True, "integer"),
    ],
)
def test_hub_agent_response_adapter_rejects_invalid_payloads(
    payload,
    is_terminal,
    error,
):
    event = HubAgentResponseInternal(
        hub_id="hub-1",
        agent_id="agent-1",
        task_id="task-1",
        room_id="room-1",
        is_terminal=is_terminal,
        timestamp=utcnow(),
        payload=payload,
    )

    with pytest.raises(ValueError, match=error):
        hub_agent_response_internal_to_agent_event(event)


def test_hub_agent_response_adapter_normalizes_interactive_state():
    event = HubAgentResponseInternal(
        hub_id="hub-1",
        agent_id="agent-1",
        task_id="task-1",
        room_id="room-1",
        is_terminal=False,
        timestamp=utcnow(),
        payload={
            "event_type": "input_required",
            "message_id": "msg-1",
            "state": "input_required",
        },
    )

    agent_event = hub_agent_response_internal_to_agent_event(event)

    assert agent_event.kind == "interactive"
    assert agent_event.state == "input-required"


def test_hub_agent_response_adapter_normalizes_legacy_processing_input_required_state():
    event = HubAgentResponseInternal(
        hub_id="hub-1",
        agent_id="agent-1",
        task_id="task-1",
        room_id="room-1",
        is_terminal=False,
        timestamp=utcnow(),
        payload={
            "kind": "processing_status",
            "message_id": "msg-1",
            "state": "input_required",
            "lifecycle_message_id": "user-msg-1",
            "lifecycle_message_id_verified": True,
        },
    )

    agent_event = hub_agent_response_internal_to_agent_event(event)

    assert agent_event.kind == "processing_status"
    assert agent_event.state == "awaiting_input"
