"""C2 step 8a: the public runs/run_events mirror is retired."""

from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from common.config.loader import REMOVED_FIELDS, Settings


@pytest.mark.parametrize(
    "module",
    [
        "execution.run_command_handler",
        "execution.repository.mongo",
        "execution.run_lifecycle",
        "execution.run_lifecycle_service",
        "execution.run_queries",
        "execution.terminal_projection",
        "execution.run_reducer",
        "execution.run_lifecycle_outcome",
        "execution.state.task_status_mapping",
    ],
)
def test_runs_mirror_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_execution_facade_reads_runs_only_from_the_canonical_reader():
    from common.protocols import ExecutionEngine
    from execution.facade import ExecutionFacade

    parameters = inspect.signature(ExecutionFacade).parameters
    for name in ("run_lifecycle", "run_reader", "run_event_enabled"):
        assert name not in parameters, name
    for name in ("get_run", "heal_diverged_runs"):
        assert not hasattr(ExecutionFacade, name), name
        assert not hasattr(ExecutionEngine, name), name


@pytest.mark.asyncio
async def test_latest_runs_for_rooms_come_from_the_bound_active_reader():
    from execution.facade import ExecutionFacade

    facade = object.__new__(ExecutionFacade)
    reader = SimpleNamespace(
        get_latest_runs_for_rooms=AsyncMock(return_value={"room-1": "run"})
    )
    facade._active_run_reader = reader

    assert await facade.get_latest_runs_for_rooms(["room-1"]) == {"room-1": "run"}
    reader.get_latest_runs_for_rooms.assert_awaited_once_with(["room-1"])


def test_execution_ports_drop_the_mirror_contracts():
    import execution.ports as ports

    for name in ("RunLifecyclePort", "RunReadPort", "RunEventEnabled"):
        assert not hasattr(ports, name), name


def test_processing_status_emit_has_no_lifecycle_write_path():
    import execution.events as events

    parameters = inspect.signature(events.emit_room_processing_status).parameters
    for name in ("run_lifecycle", "run_event_enabled", "record_lifecycle"):
        assert name not in parameters, name
    for name in (
        "run_event_notification_from_payload",
        "_legacy_run_event_wire_type",
        "_build_terminal_projection",
        "_write_terminal_lifecycle",
    ):
        assert not hasattr(events, name), name


@pytest.mark.asyncio
async def test_room_processing_status_emits_the_public_event():
    from execution.events import emit_room_processing_status

    publisher = SimpleNamespace(emit=AsyncMock())
    resolver = SimpleNamespace(
        resolve_client_request_id=AsyncMock(return_value="client-1")
    )

    await emit_room_processing_status(
        room_id="room-1",
        status="processing",
        message_id="message-1",
        event_publisher=publisher,
        client_request_id_resolver=resolver,
        details="Working",
    )

    event = publisher.emit.await_args.args[0]
    assert event.room_id == "room-1"
    assert event.message_id == "message-1"
    assert event.status == "processing"
    assert event.client_request_id == "client-1"
    assert event.details == {"message": "Working"}


def test_delivery_publisher_has_no_projection_settlement_seam():
    import delivery.event_publisher as event_publisher

    assert not hasattr(event_publisher, "ProjectionSettlementReader")


def test_hitl_terminal_adapter_has_no_mirror_dependency():
    from approval.adapters import HITLTerminalLifecycleAdapter

    parameters = inspect.signature(HITLTerminalLifecycleAdapter).parameters

    assert "run_lifecycle" not in parameters


@pytest.mark.asyncio
async def test_unhandled_hitl_termination_no_longer_writes_a_mirror_run():
    from approval.adapters import HITLTerminalLifecycleAdapter

    terminalizer = AsyncMock(return_value=False)
    adapter = HITLTerminalLifecycleAdapter(canonical_terminalizer=terminalizer)
    request = SimpleNamespace(orchestration_run_id="run-1")

    await adapter.terminalize_owning_run(
        request, terminal_status="canceled", reason="why"
    )

    terminalizer.assert_awaited_once_with(
        request, terminal_status="canceled", reason="why"
    )


def test_stale_checker_no_longer_cleans_processing_status():
    import jobs.stale_task_checker as checker

    assert not hasattr(checker, "StaleTaskCheckerDeps")
    assert not hasattr(checker.StaleTaskChecker, "_cleanup_stuck_processing_status")


def test_task_lifecycle_store_keeps_only_client_request_id_correlation():
    from conversation.repository import ClientRequestIdStore

    public = {
        name
        for name, member in inspect.getmembers(ClientRequestIdStore)
        if callable(member) and not name.startswith("__")
    }

    assert public == {
        "resolve_client_request_id_for_agent_message",
        "resolve_client_request_id_for_message_id",
    }
    assert "runs" not in inspect.signature(ClientRequestIdStore).parameters


def test_processing_message_id_field_is_gone():
    from common.dto.room import RoomInfo
    from models.room import Room

    for model in (Room, RoomInfo):
        assert "processing_message_id" not in model.model_fields, model


def test_run_event_sse_setting_is_retired():
    assert "feature_run_event_sse" in REMOVED_FIELDS
    assert "feature_run_event_sse" not in Settings.model_fields


def test_container_has_no_mirror_wiring():
    import container

    source = inspect.getsource(container)

    for name in (
        "RunCommandHandler",
        "RunLifecycleAdapter",
        "TerminalProjectionFinalizer",
        "create_execution_repositories",
        "run_event_sse_enabled",
        "heal_diverged_runs",
        "_ensure_run_lifecycle_indexes",
        '"run_events"',
    ):
        assert name not in source, name
