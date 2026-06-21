import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from common.dto import ProcessingStatusEvent, RunEventNotification
from delivery.translator import to_sse_frame
from execution.client_request_id import SSEClientRequestIdResolver
from execution.events import (
    _normalize_processing_status,
    emit_processing_status,
    emit_room_processing_status,
    run_event_notification_from_payload,
)

NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def make_client_request_id_resolver():
    resolver = AsyncMock()
    resolver.resolve_client_request_id = AsyncMock(
        side_effect=lambda message_id, provided: provided or f"resolved-{message_id}"
    )
    return resolver


@pytest.mark.asyncio
async def test_app_shell_client_request_id_resolver_uses_db_not_sse_private_method():
    db = AsyncMock()
    db.resolve_client_request_id_for_message_id = AsyncMock(return_value="cr-db")
    resolver = SSEClientRequestIdResolver(resolver=db)

    result = await resolver.resolve_client_request_id("msg-1", None)

    assert result == "cr-db"
    db.resolve_client_request_id_for_message_id.assert_awaited_once_with("msg-1")


@pytest.mark.asyncio
async def test_app_shell_client_request_id_resolver_prefers_provided_id():
    db = AsyncMock()
    db.resolve_client_request_id_for_message_id = AsyncMock(return_value="cr-db")
    resolver = SSEClientRequestIdResolver(resolver=db)

    result = await resolver.resolve_client_request_id("msg-1", "cr-provided")

    assert result == "cr-provided"
    db.resolve_client_request_id_for_message_id.assert_not_awaited()


def test_execution_processing_status_call_sites_use_event_helper():
    violations: list[str] = []
    for path in sorted((ROOT / "execution").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(), filename=rel)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "send_processing_status"
            ):
                violations.append(f"{rel}:{node.lineno}")
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "execution.run_lifecycle_service"
            ):
                violations.append(f"{rel}:{node.lineno} imports run_lifecycle_service")
    assert violations == []


def test_webhook_response_handler_binds_hitl_and_processing_status_deps():
    tree = ast.parse((ROOT / "container.py").read_text(), filename="container.py")
    factory = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "create_webhook_transport"
    )
    handler_call = next(
        node
        for node in ast.walk(factory)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AgentResponseHandler"
    )
    kwargs = {kw.arg: ast.unparse(kw.value) for kw in handler_call.keywords}

    assert kwargs["hitl_coordinator"] == "hitl_service"
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and ast.unparse(node.func.value) == "handler"
        and node.func.attr == "bind_execution_event_deps"
        and node.args
        and ast.unparse(node.args[0]) == "emit_room_processing_status"
        for node in ast.walk(factory)
    )


def _qualified_function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=path.as_posix())
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        owner = parent.get(node)
        if isinstance(owner, ast.ClassDef):
            names.add(f"{owner.name}.{node.name}")
        else:
            names.add(node.name)
    return names


def test_execution_event_manifest_covers_status_helpers_and_hub_ingress():
    manifest_path = ROOT / "tests/fixtures/phase7_execution_event_callers.json"
    manifest = json.loads(manifest_path.read_text())
    required_keys = {
        "path",
        "function",
        "role",
        "status_source",
        "lifecycle_id_source",
        "frontend_transport",
        "lifecycle_required_for_frontend_emit",
    }

    for entry in manifest:
        assert set(entry) == required_keys
        assert entry["lifecycle_required_for_frontend_emit"] is True
        assert entry["status_source"]
        assert "lifecycle" in entry["lifecycle_id_source"]
        assert entry["frontend_transport"]
        assert "legacy" not in entry["frontend_transport"].lower()
        assert "compatibility" not in entry["frontend_transport"].lower()
        assert entry["function"] in _qualified_function_names(ROOT / entry["path"])

    expected = {
        ("execution/events.py", "emit_processing_status"),
        (
            "execution/dispatch/response_handler.py",
            "AgentResponseHandler._emit_processing_status",
        ),
        (
            "execution/orchestration/queue_executor.py",
            "QueueExecutor._emit_processing_status",
        ),
        (
            "execution/orchestration/room_message_center.py",
            "RoomMessageCenter._emit_processing_status",
        ),
        (
            "execution/orchestration/supervisor_executor.py",
            "SupervisorExecutor._emit_processing_status",
        ),
        (
            "execution/facade.py",
            "ExecutionFacade._emit_room_preflight_processing_status",
        ),
        (
            "execution/facade.py",
            "ExecutionFacade._emit_room_preflight_terminal_status",
        ),
        ("execution/facade.py", "hub_agent_response_internal_to_agent_event"),
    }
    actual = {(entry["path"], entry["function"]) for entry in manifest}
    assert actual == expected

    facade_source = (ROOT / "execution/facade.py").read_text()
    assert "requires verified lifecycle_message_id" in facade_source


@pytest.mark.asyncio
async def test_emit_processing_status_records_run_event_then_processing_status():
    lifecycle = AsyncMock()
    lifecycle.record_processing_status.return_value = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 2,
        "type": "run_started",
        "payload": {"state": "processing"},
    }
    publisher = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
        client_request_id="cr-1",
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: True,
        client_request_id_resolver=resolver,
    )

    lifecycle.record_processing_status.assert_awaited_once()
    assert [call.args[0].event_type for call in publisher.emit.await_args_list] == [
        "run_event",
        "processing_status",
    ]


@pytest.mark.asyncio
async def test_emit_processing_status_routes_awaiting_input_to_typed_event():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="awaiting_input",
        message_id="msg-1",
        details={"prompt": "Need input"},
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: False,
        client_request_id_resolver=resolver,
    )

    lifecycle.record_processing_status.assert_awaited_once()
    event = publisher.emit.await_args.args[0]
    assert isinstance(event, ProcessingStatusEvent)
    assert event.status == "awaiting_input"
    assert event.details == {"prompt": "Need input"}


@pytest.mark.asyncio
async def test_emit_room_processing_status_normalizes_legacy_string_details():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_room_processing_status(
        room_id="room-1",
        status="failed",
        message_id="msg-1",
        lifecycle_message_id="msg-1",
        client_request_id="cr-1",
        details="parse failed",
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: False,
        client_request_id_resolver=resolver,
    )

    lifecycle.record_processing_status.assert_awaited_once()
    assert lifecycle.record_processing_status.await_args.kwargs["details"] == {
        "message": "parse failed"
    }
    assert (
        lifecycle.record_processing_status.await_args.kwargs["error_message"]
        == "parse failed"
    )
    event = publisher.emit.await_args.args[0]
    assert isinstance(event, ProcessingStatusEvent)
    assert event.details == {"message": "parse failed"}


@pytest.mark.asyncio
async def test_emit_processing_status_uses_typed_event_for_all_final_statuses():
    publisher = AsyncMock()
    run_lifecycle = AsyncMock()
    run_lifecycle.record_processing_status.return_value = None
    resolver = AsyncMock()
    resolver.resolve_client_request_id.return_value = "cr-1"

    statuses = [
        "queued",
        "processing",
        "awaiting_input",
        "completed",
        "failed",
        "canceled",
        "rejected",
        "rate_limited",
        "error",
    ]
    for status in statuses:
        await emit_processing_status(
            room_id="room-1",
            status=status,
            message_id="msg-1",
            run_lifecycle=run_lifecycle,
            event_publisher=publisher,
            run_event_enabled=lambda: False,
            client_request_id_resolver=resolver,
            details={"status": status},
        )

    emitted = [call.args[0] for call in publisher.emit.await_args_list]
    assert [event.status for event in emitted] == statuses


@pytest.mark.asyncio
async def test_emit_processing_status_rejects_removed_details_parameter():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    resolver = make_client_request_id_resolver()
    removed_kwarg = "legacy" + "_details"

    with pytest.raises(TypeError):
        await emit_processing_status(
            room_id="room-1",
            status="failed",
            message_id="msg-1",
            error_message="agent failed",
            run_lifecycle=lifecycle,
            event_publisher=publisher,
            run_event_enabled=lambda: False,
            client_request_id_resolver=resolver,
            **{removed_kwarg: "agent failed"},
        )

    publisher.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_emit_processing_status_resolves_client_request_id_when_omitted():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: False,
        client_request_id_resolver=resolver,
    )

    resolver.resolve_client_request_id.assert_awaited_once_with("msg-1", None)
    event = publisher.emit.await_args.args[0]
    assert event.client_request_id == "resolved-msg-1"


@pytest.mark.asyncio
async def test_emit_processing_status_keeps_run_event_correlation_explicit_only():
    lifecycle = AsyncMock()
    lifecycle.record_processing_status.return_value = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 1,
        "type": "run_started",
        "payload": {"state": "processing"},
    }
    publisher = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
        client_request_id=None,
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: True,
        client_request_id_resolver=resolver,
    )

    run_event, processing_status = [
        call.args[0] for call in publisher.emit.await_args_list
    ]
    assert run_event.correlation_id is None
    assert processing_status.client_request_id == "resolved-msg-1"


@pytest.mark.asyncio
async def test_emit_processing_status_resolver_failure_does_not_skip_lifecycle():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    resolver = AsyncMock()
    resolver.resolve_client_request_id.side_effect = RuntimeError("db down")

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
        client_request_id=None,
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: False,
        client_request_id_resolver=resolver,
    )

    lifecycle.record_processing_status.assert_awaited_once()
    assert (
        lifecycle.record_processing_status.await_args.kwargs["client_request_id"]
        is None
    )
    event = publisher.emit.await_args.args[0]
    assert event.client_request_id is None


@pytest.mark.asyncio
async def test_emit_processing_status_separates_frontend_and_lifecycle_ids():
    lifecycle = AsyncMock()
    lifecycle.record_processing_status.return_value = {
        "event_id": "evt-1",
        "run_id": "user-msg-1",
        "seq": 1,
        "type": "run_started",
        "payload": {},
    }
    publisher = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="agent-msg-1",
        lifecycle_message_id="user-msg-1",
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: False,
        client_request_id_resolver=resolver,
    )

    lifecycle.record_processing_status.assert_awaited_once()
    assert lifecycle.record_processing_status.await_args.args[2] == "user-msg-1"
    event = publisher.emit.await_args.args[0]
    assert event.message_id == "agent-msg-1"


@pytest.mark.asyncio
async def test_emit_processing_status_can_skip_lifecycle_for_frontend_only_paths():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="agent-msg-1",
        lifecycle_message_id=None,
        record_lifecycle=False,
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: True,
        client_request_id_resolver=resolver,
    )

    lifecycle.record_processing_status.assert_not_awaited()
    assert publisher.emit.await_args.args[0].message_id == "agent-msg-1"


@pytest.mark.asyncio
async def test_emit_processing_status_keeps_typed_frame_when_lifecycle_noops():
    lifecycle = AsyncMock()
    lifecycle.record_processing_status.return_value = None
    publisher = AsyncMock()
    resolver = make_client_request_id_resolver()

    result = await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
        record_lifecycle=True,
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: True,
        client_request_id_resolver=resolver,
    )

    assert result is None
    lifecycle.record_processing_status.assert_awaited_once()
    publisher.emit.assert_awaited_once()
    assert publisher.emit.await_args.args[0].event_type == "processing_status"


@pytest.mark.asyncio
async def test_emit_processing_status_keeps_typed_final_status_when_lifecycle_noops():
    lifecycle = AsyncMock()
    lifecycle.record_processing_status.return_value = None
    publisher = AsyncMock()
    resolver = make_client_request_id_resolver()

    result = await emit_processing_status(
        room_id="room-1",
        status="awaiting_input",
        message_id="msg-1",
        record_lifecycle=True,
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: True,
        client_request_id_resolver=resolver,
    )

    assert result is None
    lifecycle.record_processing_status.assert_awaited_once()
    publisher.emit.assert_awaited_once()
    assert publisher.emit.await_args.args[0].status == "awaiting_input"


def test_run_event_notification_from_payload_maps_legacy_payload():
    payload = {
        "event_id": "evt-1",
        "run_id": "run-1",
        "seq": 7,
        "type": "run_completed",
        "payload": {"state": "completed"},
        "correlation_id": "payload-cr",
    }

    event = run_event_notification_from_payload(
        room_id="room-1",
        payload=payload,
        correlation_id="fallback-cr",
    )

    assert event.event_id == "evt-1"
    assert event.run_id == "run-1"
    assert event.seq == 7
    assert event.run_event_type == "run_completed"
    assert event.payload == {"state": "completed"}
    assert event.correlation_id == "payload-cr"


def test_run_event_delivery_translation_preserves_correlation_id():
    event = RunEventNotification(
        room_id="room-1",
        event_id="evt-1",
        run_id="run-1",
        seq=1,
        run_event_type="run_started",
        payload={"state": "processing"},
        correlation_id="cr-1",
    )

    sse = to_sse_frame(event, timestamp=NOW)
    assert sse["type"] == "run_event"
    assert sse["data"]["event_id"] == "evt-1"
    assert sse["data"]["run_id"] == "run-1"
    assert sse["data"]["seq"] == 1
    assert sse["data"]["type"] == "run_started"
    assert sse["data"]["payload"] == {"state": "processing"}
    assert sse["data"]["correlation_id"] == "cr-1"


def test_run_event_delivery_translation_preserves_null_correlation_key():
    event = RunEventNotification(
        room_id="room-1",
        event_id="evt-1",
        run_id="run-1",
        seq=1,
        run_event_type="run_started",
        payload={},
        correlation_id=None,
    )

    sse = to_sse_frame(event, timestamp=NOW)
    assert "correlation_id" in sse["data"]
    assert sse["data"]["correlation_id"] is None


def test_processing_status_delivery_translation_preserves_final_sse_shape():
    event = ProcessingStatusEvent(
        room_id="room-1",
        message_id="msg-1",
        status="processing",
        details=None,
        client_request_id="cr-1",
        agents=[{"agent_id": "agent-1", "agent_name": "Agent One"}],
    )
    sse = to_sse_frame(event, timestamp=NOW)
    assert sse["type"] == "processing_status"
    assert sse["data"]["status"] == "processing"
    assert sse["data"]["message_id"] == "msg-1"
    assert "details" in sse["data"]
    assert sse["data"]["details"] is None
    assert "timestamp" not in sse["data"]
    assert sse["data"]["client_request_id"] == "cr-1"
    assert sse["data"]["agents"] == [{"agent_id": "agent-1", "agent_name": "Agent One"}]


def test_processing_status_delivery_translation_preserves_structured_details():
    event = ProcessingStatusEvent(
        room_id="room-1",
        message_id="msg-1",
        status="failed",
        details={"message": "agent failed"},
        client_request_id=None,
        agents=None,
    )
    sse = to_sse_frame(event, timestamp=NOW)
    assert sse["type"] == "processing_status"
    assert sse["data"]["details"] == {"message": "agent failed"}
    assert "timestamp" not in sse["data"]
    assert "client_request_id" not in sse["data"]
    assert "agents" not in sse["data"]


def test_normalize_processing_status_accepts_string_and_enum_values():
    from common.a2a_constants import SSEProcessingStatus

    assert _normalize_processing_status("processing") == "processing"
    assert _normalize_processing_status("awaiting_input") == "awaiting_input"
    assert _normalize_processing_status("rate_limited") == "rate_limited"
    assert _normalize_processing_status(SSEProcessingStatus.COMPLETED) == "completed"


def test_unsupported_processing_status_is_rejected():
    with pytest.raises(ValueError):
        _normalize_processing_status("not-a-status")


def test_run_event_notification_from_payload_rejects_missing_required_fields():
    with pytest.raises(ValueError, match="event_id"):
        run_event_notification_from_payload(
            room_id="room-1",
            payload={
                "run_id": "run-1",
                "seq": 1,
                "type": "run_started",
                "payload": {},
            },
        )


@pytest.mark.asyncio
async def test_emit_processing_status_rejects_missing_frontend_message_id_for_typed_status():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    with pytest.raises(ValueError, match="frontend message_id"):
        await emit_processing_status(
            room_id="room-1",
            status="processing",
            message_id=None,
            lifecycle_message_id="run-1",
            run_lifecycle=lifecycle,
            event_publisher=publisher,
            run_event_enabled=lambda: False,
            client_request_id_resolver=make_client_request_id_resolver(),
        )
    lifecycle.record_processing_status.assert_not_awaited()
    publisher.emit.assert_not_awaited()
