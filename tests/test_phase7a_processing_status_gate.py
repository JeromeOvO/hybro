"""Phase 7a production processing-status ownership gate.

The manifest is intentionally strict: every production ``send_processing_status``
call must either record run lifecycle state immediately before delivery, emit a
pre-recorded run event payload before delivery, or document why the call is
transport-only.
"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "phase7a_processing_status_callers.json"
PRODUCTION_ROOTS = ("api", "app_shell", "jobs", "execution")
OBSOLETE_CALL_IDS = {
    "api.sse.cancel_message.canceled",
}
DELETED_PACKAGE_PATH_PREFIXES = ("config/", "infrastructure/", "modules/", "services/")
ROOM_RUNTIME_STATUS_EMITTERS = {
    "_emit_processing_status_event",
    "_send_processing_status",
}


def _unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    return ast.unparse(node).strip()


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _arg_or_kw(call: ast.Call, index: int, name: str) -> ast.AST | None:
    if len(call.args) > index:
        return call.args[index]
    return _keyword(call, name)


def _is_awaited(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.Await):
            return True
        current = parents.get(current)
    return False


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _call_expression(call: ast.Call) -> str:
    return _unparse(call) or ""


def _iter_child_calls(stmt: ast.AST, parents: dict[ast.AST, ast.AST]) -> list[ast.Call]:
    return [node for node in ast.walk(stmt) if isinstance(node, ast.Call)]


def _find_awaited_call(
    stmt: ast.AST,
    parents: dict[ast.AST, ast.AST],
    *,
    name: str,
) -> ast.Call | None:
    for call in _iter_child_calls(stmt, parents):
        if _call_name(call) == name and _is_awaited(call, parents):
            return call
    return None


def _direct_awaited_call(stmt: ast.stmt, *, name: str) -> ast.Call | None:
    value: ast.AST | None = None
    if isinstance(stmt, ast.Expr):
        value = stmt.value
    elif isinstance(stmt, ast.Assign):
        value = stmt.value
    if not isinstance(value, ast.Await) or not isinstance(value.value, ast.Call):
        return None
    call = value.value
    if _call_name(call) != name:
        return None
    return call


def _build_parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_function(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> str:
    names: list[str] = []
    current: ast.AST | None = call
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(current.name)
        elif isinstance(current, ast.ClassDef):
            names.append(current.name)
        current = parents.get(current)
    names.reverse()
    return ".".join(names)


def _statement_context(
    call: ast.Call,
    parents: dict[ast.AST, ast.AST],
) -> tuple[list[ast.stmt], int]:
    current: ast.AST = call
    while current in parents:
        parent = parents[current]
        for _field, value in ast.iter_fields(parent):
            if isinstance(value, list) and current in value:
                if all(isinstance(item, ast.stmt) for item in value):
                    return value, value.index(current)  # type: ignore[arg-type]
        current = parent
    raise AssertionError(f"Could not find statement context for line {call.lineno}")


def _expr_equal(left: str | None, right: str | None) -> bool:
    if left == right:
        return True
    if left is None or right is None:
        return False
    try:
        return ast.unparse(ast.parse(left, mode="eval")).strip() == ast.unparse(
            ast.parse(right, mode="eval")
        ).strip()
    except SyntaxError:
        return False


@dataclass(frozen=True)
class ProcessingStatusCall:
    path: str
    function_or_method: str
    line: int
    room_id_expression: str | None
    status_expression: str | None
    sse_message_id_expression: str | None
    client_request_id_expression: str | None
    details_expression: str | None
    delivery_expression: str | None
    emitter_kind: str
    call: ast.Call
    parents: dict[ast.AST, ast.AST]

    def matches_manifest_entry(self, entry: dict[str, Any]) -> bool:
        if self.path != entry.get("path"):
            return False
        if self.function_or_method != entry.get("function_or_method"):
            return False
        fields = (
            "room_id_expression",
            "status_expression",
            "sse_message_id_expression",
            "client_request_id_expression",
            "details_expression",
            "delivery_expression",
        )
        return all(_expr_equal(getattr(self, field), entry.get(field)) for field in fields)


def _production_files() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCTION_ROOTS:
        files.extend((ROOT / root).rglob("*.py"))
    return sorted(set(files))


def _discover_calls() -> list[ProcessingStatusCall]:
    calls: list[ProcessingStatusCall] = []
    for path in _production_files():
        rel_path = path.relative_to(ROOT).as_posix()
        if rel_path in {
            "execution/legacy_processing_status.py",
        }:
            continue
        tree = ast.parse(path.read_text(), filename=rel_path)
        parents = _build_parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            call_name = node.func.attr
            if call_name == "send_processing_status":
                room_id_expression = _unparse(_arg_or_kw(node, 0, "room_id"))
                status_expression = _unparse(_arg_or_kw(node, 1, "status"))
                sse_message_id_expression = _unparse(_arg_or_kw(node, 2, "message_id"))
                client_request_id_expression = _unparse(
                    _keyword(node, "client_request_id")
                )
                details_expression = _unparse(_keyword(node, "details"))
                delivery_expression = _unparse(node.func.value)
                emitter_kind = "direct_transport"
                if (
                    rel_path == "app_shell/room_runtime.py"
                    and _enclosing_function(node, parents)
                    == "RoomServices._emit_processing_status_event"
                ):
                    status_expression = "status"
                    client_request_id_expression = "client_request_id"
                    details_expression = "details"
            elif (
                rel_path == "app_shell/room_runtime.py"
                and call_name in ROOM_RUNTIME_STATUS_EMITTERS
            ):
                room_id_expression = _unparse(_arg_or_kw(node, 0, "room_id"))
                if call_name == "_send_processing_status":
                    status_expression = "SSEProcessingStatus.PROCESSING"
                    sse_message_id_expression = _unparse(
                        _arg_or_kw(node, 1, "message_id")
                    )
                    client_request_id_expression = _unparse(
                        _arg_or_kw(node, 2, "client_request_id")
                    )
                    details_expression = None
                    delivery_expression = "self.sse_manager"
                else:
                    status_expression = _unparse(_arg_or_kw(node, 1, "status"))
                    sse_message_id_expression = _unparse(
                        _arg_or_kw(node, 2, "message_id")
                    )
                    client_request_id_expression = _unparse(
                        _keyword(node, "client_request_id")
                    )
                    details_expression = _unparse(_keyword(node, "details"))
                    delivery_expression = "self.sse_manager"
                emitter_kind = call_name
            else:
                continue
            calls.append(
                ProcessingStatusCall(
                    path=rel_path,
                    function_or_method=_enclosing_function(node, parents),
                    line=node.lineno,
                    room_id_expression=room_id_expression,
                    status_expression=status_expression,
                    sse_message_id_expression=sse_message_id_expression,
                    client_request_id_expression=client_request_id_expression,
                    details_expression=details_expression,
                    delivery_expression=delivery_expression,
                    emitter_kind=emitter_kind,
                    call=node,
                    parents=parents,
                )
            )
    return sorted(calls, key=lambda c: (c.path, c.line))


def _load_manifest() -> list[dict[str, Any]]:
    return json.loads(MANIFEST.read_text())


def _prior_statements(item: ProcessingStatusCall) -> list[ast.stmt]:
    body, index = _statement_context(item.call, item.parents)
    return body[:index]


def _find_prior_awaited_helper(
    item: ProcessingStatusCall,
    helper_name: str,
) -> ast.Call | None:
    for stmt in reversed(_prior_statements(item)):
        call = _direct_awaited_call(stmt, name=helper_name)
        if call is not None:
            return call
    return None


def _assert_lifecycle_helper_matches(
    item: ProcessingStatusCall,
    entry: dict[str, Any],
) -> None:
    if item.emitter_kind in ROOM_RUNTIME_STATUS_EMITTERS:
        record_lifecycle = _keyword(item.call, "record_lifecycle")
        assert record_lifecycle is None or _expr_equal(
            _unparse(record_lifecycle), "True"
        ), f"{entry['call_id']} disables lifecycle recording"
        return

    helper = _find_prior_awaited_helper(item, "record_and_maybe_broadcast_run_event")
    if (
        helper is None
        and item.path == "app_shell/room_runtime.py"
        and item.function_or_method == "RoomServices._emit_processing_status_event"
    ):
        for stmt in reversed(_prior_statements(item)):
            if not isinstance(stmt, ast.If) or not _expr_equal(
                _unparse(stmt.test), "record_lifecycle"
            ):
                continue
            for child in stmt.body:
                helper = _direct_awaited_call(
                    child,
                    name="record_and_maybe_broadcast_run_event",
                )
                if helper is not None:
                    break
            if helper is not None:
                break
    assert helper is not None, f"{entry['call_id']} missing awaited lifecycle helper"
    assert _expr_equal(_unparse(_arg_or_kw(helper, 0, "room_id")), entry["room_id_expression"])
    assert _expr_equal(_unparse(_arg_or_kw(helper, 1, "status")), entry["status_expression"])
    assert _expr_equal(
        _unparse(_arg_or_kw(helper, 2, "message_id")),
        entry["lifecycle_message_id_expression"],
    )
    assert _expr_equal(_unparse(_keyword(helper, "details")), entry["details_expression"])
    assert _expr_equal(
        _unparse(_keyword(helper, "client_request_id")),
        entry["client_request_id_expression"],
    )
    assert _expr_equal(_unparse(_keyword(helper, "sse")), entry["delivery_expression"])


def _assert_no_prior_lifecycle_work(item: ProcessingStatusCall, call_id: str) -> None:
    if item.emitter_kind in ROOM_RUNTIME_STATUS_EMITTERS:
        record_lifecycle = _keyword(item.call, "record_lifecycle")
        assert record_lifecycle is not None and _expr_equal(
            _unparse(record_lifecycle), "False"
        ), f"{call_id} transport-only helper call must pass record_lifecycle=False"
        return

    helper = _find_prior_awaited_helper(item, "record_and_maybe_broadcast_run_event")
    broadcast = _find_prior_awaited_helper(item, "broadcast_run_event_payload")
    assert helper is None, f"{call_id} is transport-only but has lifecycle helper"
    assert broadcast is None, f"{call_id} is transport-only but has run_event broadcast"


def _assert_pre_recorded_payload(item: ProcessingStatusCall, entry: dict[str, Any]) -> None:
    prior = _prior_statements(item)

    def assignment_index(target_expr: str, value_expr: str) -> int | None:
        for idx, stmt in enumerate(prior):
            if not isinstance(stmt, ast.Assign):
                continue
            if not any(_expr_equal(_unparse(target), target_expr) for target in stmt.targets):
                continue
            if _expr_equal(_unparse(stmt.value), value_expr):
                return idx
        return None

    tid_index = assignment_index("tid", "doc.get('trigger_message_id') or run_id")
    assert tid_index is not None, (
        f"{entry['call_id']} missing trigger message assignment before send"
    )
    assert _expr_equal(item.sse_message_id_expression, "str(tid)"), (
        f"{entry['call_id']} send must use trigger message id tid"
    )

    client_request_index = assignment_index(
        "client_request_id", "doc.get('client_request_id')"
    )
    assert client_request_index is not None, (
        f"{entry['call_id']} missing client_request_id assignment before send"
    )
    assert _expr_equal(item.client_request_id_expression, "client_request_id"), (
        f"{entry['call_id']} send must use assigned client_request_id"
    )

    payload_index: int | None = None
    for idx, stmt in enumerate(prior):
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(
            _expr_equal(_unparse(target), entry["payload_variable"])
            for target in stmt.targets
        ):
            continue
        if (
            isinstance(stmt.value, ast.Await)
            and isinstance(stmt.value.value, ast.Call)
            and _expr_equal(
                _unparse(stmt.value.value),
                entry["pre_record_call_expression"],
            )
        ):
            payload_index = idx
            break
    assert payload_index is not None, (
        f"{entry['call_id']} missing awaited pre-record assignment"
    )

    guard_test = (
        entry["payload_none_guard"].removeprefix("if ").removesuffix(": continue")
    )
    guard_index: int | None = None
    for idx, stmt in enumerate(prior):
        if not isinstance(stmt, ast.If):
            continue
        if not _expr_equal(_unparse(stmt.test), guard_test):
            continue
        if any(isinstance(child, ast.Continue) for child in stmt.body):
            guard_index = idx
            break
    assert guard_index is not None, (
        f"{entry['call_id']} missing payload None continue guard"
    )
    assert payload_index < guard_index, (
        f"{entry['call_id']} payload None guard must occur after payload assignment"
    )

    metric_index: int | None = None
    for idx, stmt in enumerate(prior):
        if idx <= payload_index:
            continue
        if any(
            isinstance(node, ast.Call) and _call_name(node) == "increment_counter"
            for node in ast.walk(stmt)
        ):
            metric_index = idx
            break
    assert metric_index is not None, (
        f"{entry['call_id']} missing watchdog metric increment"
    )
    assert guard_index < metric_index, (
        f"{entry['call_id']} payload None guard must occur before metric increment"
    )

    broadcast_index: int | None = None
    broadcast: ast.Call | None = None
    for idx, stmt in enumerate(prior):
        candidate = _direct_awaited_call(stmt, name="broadcast_run_event_payload")
        if candidate is not None:
            broadcast_index = idx
            broadcast = candidate
            break
    assert broadcast is not None and broadcast_index is not None, (
        f"{entry['call_id']} missing run_event payload broadcast"
    )
    assert guard_index < broadcast_index, (
        f"{entry['call_id']} payload None guard must occur before run_event broadcast"
    )
    assert _expr_equal(
        _call_expression(broadcast),
        entry["run_event_broadcast_expression"],
    )
    assert _expr_equal(_unparse(_keyword(broadcast, "sse")), entry["delivery_expression"])


def test_production_processing_status_callers_are_manifest_covered() -> None:
    manifest = _load_manifest()
    discovered = _discover_calls()

    missing_ids = [entry.get("call_id") for entry in manifest if not entry.get("call_id")]
    assert not missing_ids, "Every manifest entry must have a stable call_id"

    matched_call_ids: set[int] = set()
    errors: list[str] = []

    for entry in manifest:
        if entry.get("call_id") in OBSOLETE_CALL_IDS:
            continue
        matches = [
            call
            for call in discovered
            if id(call) not in matched_call_ids and call.matches_manifest_entry(entry)
        ]
        if not matches:
            errors.append(f"{entry['call_id']}: no matching production call found")
            continue
        if len(matches) > 1:
            exact_line = [call for call in matches if call.line == entry.get("line")]
            matches = exact_line or matches
        if len(matches) != 1:
            errors.append(
                f"{entry['call_id']}: ambiguous production call match "
                f"{[(call.path, call.line) for call in matches]}"
            )
            continue

        call = matches[0]
        matched_call_ids.add(id(call))
        recording_kind = entry.get("recording_kind")
        if entry.get("requires_recording"):
            if recording_kind != "record_processing_status":
                errors.append(f"{entry['call_id']}: invalid recording_kind for lifecycle call")
                continue
            try:
                _assert_lifecycle_helper_matches(call, entry)
            except AssertionError as exc:
                errors.append(str(exc))
        elif recording_kind == "pre_recorded_payload":
            try:
                _assert_pre_recorded_payload(call, entry)
            except AssertionError as exc:
                errors.append(str(exc))
        elif recording_kind == "transport_only":
            if not entry.get("transport_only_reason"):
                errors.append(f"{entry['call_id']}: transport-only entry needs a reason")
                continue
            try:
                _assert_no_prior_lifecycle_work(call, entry["call_id"])
            except AssertionError as exc:
                errors.append(str(exc))
        else:
            errors.append(f"{entry['call_id']}: unknown recording_kind {recording_kind!r}")

    unlisted = [call for call in discovered if id(call) not in matched_call_ids]
    if unlisted:
        errors.append(
            "Unlisted production send_processing_status calls:\n"
            + "\n".join(
                f"- {call.path}:{call.line} {call.function_or_method} "
                f"{call.delivery_expression}.send_processing_status("
                f"{call.room_id_expression}, {call.status_expression}, "
                f"{call.sse_message_id_expression})"
                for call in unlisted
            )
        )

    assert not errors, "\n\n".join(errors)


def test_lifecycle_helper_must_be_direct_prior_sibling_statement() -> None:
    source = """
async def send(flag):
    if flag:
        await record_and_maybe_broadcast_run_event("room", "completed", "msg", sse=sse)
    await sse.send_processing_status("room", "completed", "msg")
"""
    tree = ast.parse(source)
    parents = _build_parents(tree)
    send_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send_processing_status"
    )
    item = ProcessingStatusCall(
        path="example.py",
        function_or_method="send",
        line=send_call.lineno,
        room_id_expression=_unparse(_arg_or_kw(send_call, 0, "room_id")),
        status_expression=_unparse(_arg_or_kw(send_call, 1, "status")),
        sse_message_id_expression=_unparse(_arg_or_kw(send_call, 2, "message_id")),
        client_request_id_expression=None,
        details_expression=None,
        delivery_expression=_unparse(send_call.func.value),
        emitter_kind="direct_transport",
        call=send_call,
        parents=parents,
    )

    assert _find_prior_awaited_helper(
        item, "record_and_maybe_broadcast_run_event"
    ) is None


def _pre_recorded_item_from_source(source: str) -> ProcessingStatusCall:
    tree = ast.parse(source)
    parents = _build_parents(tree)
    send_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send_processing_status"
    )
    item = ProcessingStatusCall(
        path="example.py",
        function_or_method="fail_stale_runs",
        line=send_call.lineno,
        room_id_expression=_unparse(_arg_or_kw(send_call, 0, "room_id")),
        status_expression=_unparse(_arg_or_kw(send_call, 1, "status")),
        sse_message_id_expression=_unparse(_arg_or_kw(send_call, 2, "message_id")),
        client_request_id_expression=_unparse(_keyword(send_call, "client_request_id")),
        details_expression=_unparse(_keyword(send_call, "details")),
        delivery_expression=_unparse(send_call.func.value),
        emitter_kind="direct_transport",
        call=send_call,
        parents=parents,
    )
    return item


def _pre_recorded_entry() -> dict[str, Any]:
    return {
        "call_id": "example.fail_stale_runs.failed",
        "pre_record_call_expression": (
            "run_command_handler.append_run_timeout_failure("
            "room_id, run_id, stale_minutes=stale_mins)"
        ),
        "payload_variable": "payload",
        "payload_none_guard": "if payload is None: continue",
        "run_event_broadcast_expression": (
            "broadcast_run_event_payload("
            "room_id, payload, client_request_id=client_request_id, sse=sse_manager)"
        ),
        "delivery_expression": "sse_manager",
    }


def test_pre_recorded_payload_requires_awaited_assignment() -> None:
    source = """
async def fail_stale_runs():
    for doc in docs:
        tid = doc.get("trigger_message_id") or run_id
        client_request_id = doc.get("client_request_id")
        payload = run_command_handler.append_run_timeout_failure(
            room_id, run_id, stale_minutes=stale_mins
        )
        if payload is None:
            continue
        increment_counter("run_watchdog_forced_failure_total")
        await broadcast_run_event_payload(
            room_id,
            payload,
            client_request_id=client_request_id,
            sse=sse_manager,
        )
        await sse_manager.send_processing_status(
            room_id,
            SSEProcessingStatus.FAILED,
            str(tid),
            client_request_id=client_request_id,
            details="Run watchdog: stale non-terminal run timed out",
        )
"""
    item = _pre_recorded_item_from_source(source)
    entry = _pre_recorded_entry()

    with pytest.raises(AssertionError, match="missing awaited pre-record assignment"):
        _assert_pre_recorded_payload(item, entry)


def test_pre_recorded_payload_requires_trigger_and_client_assignments() -> None:
    source = """
async def fail_stale_runs():
    for doc in docs:
        payload = await run_command_handler.append_run_timeout_failure(
            room_id, run_id, stale_minutes=stale_mins
        )
        if payload is None:
            continue
        increment_counter("run_watchdog_forced_failure_total")
        await broadcast_run_event_payload(
            room_id,
            payload,
            client_request_id=client_request_id,
            sse=sse_manager,
        )
        await sse_manager.send_processing_status(
            room_id,
            SSEProcessingStatus.FAILED,
            str(run_id),
            client_request_id=client_request_id,
            details="Run watchdog: stale non-terminal run timed out",
        )
"""
    item = _pre_recorded_item_from_source(source)
    entry = _pre_recorded_entry()

    with pytest.raises(AssertionError, match="missing trigger message assignment"):
        _assert_pre_recorded_payload(item, entry)


def test_pre_recorded_payload_requires_guard_before_metric_and_broadcast() -> None:
    source = """
async def fail_stale_runs():
    for doc in docs:
        tid = doc.get("trigger_message_id") or run_id
        client_request_id = doc.get("client_request_id")
        payload = await run_command_handler.append_run_timeout_failure(
            room_id, run_id, stale_minutes=stale_mins
        )
        increment_counter("run_watchdog_forced_failure_total")
        if payload is None:
            continue
        await broadcast_run_event_payload(
            room_id,
            payload,
            client_request_id=client_request_id,
            sse=sse_manager,
        )
        await sse_manager.send_processing_status(
            room_id,
            SSEProcessingStatus.FAILED,
            str(tid),
            client_request_id=client_request_id,
            details="Run watchdog: stale non-terminal run timed out",
        )
"""
    item = _pre_recorded_item_from_source(source)
    entry = _pre_recorded_entry()

    with pytest.raises(AssertionError, match="payload None guard must occur"):
        _assert_pre_recorded_payload(item, entry)


def test_manifest_call_ids_do_not_encode_line_numbers() -> None:
    manifest = _load_manifest()
    line_suffixed = [
        entry["call_id"]
        for entry in manifest
        if entry.get("call_id", "").rsplit(".", 1)[-1].isdigit()
    ]
    assert not line_suffixed


def test_manifest_paths_do_not_reference_deleted_packages() -> None:
    manifest = _load_manifest()
    deleted_paths = [
        entry["path"]
        for entry in manifest
        if any(
            str(entry.get("path", "")).startswith(prefix)
            for prefix in DELETED_PACKAGE_PATH_PREFIXES
        )
    ]

    assert not deleted_paths


def test_sse_manager_processing_status_has_no_run_lifecycle_side_effects() -> None:
    if os.environ.get("PHASE7A_ALLOW_LEGACY_SSE_MANAGER") == "1":
        return

    path = ROOT / "app_shell" / "delivery_runtime.py"
    tree = ast.parse(path.read_text(), filename="app_shell/delivery_runtime.py")
    forbidden_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "execution.run_command_handler":
            forbidden_imports.extend(alias.name for alias in node.names)
    assert "run_command_handler" not in forbidden_imports
    assert "run_event_sse_enabled" not in forbidden_imports

    send_func: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "send_processing_status":
            send_func = node
            break
    assert send_func is not None

    for node in ast.walk(send_func):
        if isinstance(node, ast.Call) and _call_name(node) == "record_processing_status":
            raise AssertionError(
                f"send_processing_status still records lifecycle at line {node.lineno}"
            )
        if isinstance(node, ast.Call) and _call_name(node) == "broadcast_to_room":
            event_type = _arg_or_kw(node, 1, "message_type")
            if isinstance(event_type, ast.Constant) and event_type.value == "run_event":
                raise AssertionError(
                    f"send_processing_status still broadcasts run_event at line {node.lineno}"
                )
        if isinstance(node, ast.Call) and _call_name(node) == "run_event_sse_enabled":
            raise AssertionError(
                f"send_processing_status still checks run_event_sse_enabled at line {node.lineno}"
            )
