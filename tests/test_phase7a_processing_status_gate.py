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


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "phase7a_processing_status_callers.json"
PRODUCTION_ROOTS = ("modules", "services", "api", "jobs")


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
        tree = ast.parse(path.read_text(), filename=rel_path)
        parents = _build_parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "send_processing_status":
                continue
            calls.append(
                ProcessingStatusCall(
                    path=rel_path,
                    function_or_method=_enclosing_function(node, parents),
                    line=node.lineno,
                    room_id_expression=_unparse(_arg_or_kw(node, 0, "room_id")),
                    status_expression=_unparse(_arg_or_kw(node, 1, "status")),
                    sse_message_id_expression=_unparse(_arg_or_kw(node, 2, "message_id")),
                    client_request_id_expression=_unparse(
                        _keyword(node, "client_request_id")
                    ),
                    details_expression=_unparse(_keyword(node, "details")),
                    delivery_expression=_unparse(node.func.value),
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
    helper = _find_prior_awaited_helper(item, "record_and_maybe_broadcast_run_event")
    assert helper is not None, f"{entry['call_id']} missing awaited lifecycle helper"
    assert helper.lineno == entry.get("record_call_line"), (
        f"{entry['call_id']} record_call_line is stale: manifest "
        f"{entry.get('record_call_line')} != current {helper.lineno}"
    )
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
    helper = _find_prior_awaited_helper(item, "record_and_maybe_broadcast_run_event")
    broadcast = _find_prior_awaited_helper(item, "broadcast_run_event_payload")
    assert helper is None, f"{call_id} is transport-only but has lifecycle helper"
    assert broadcast is None, f"{call_id} is transport-only but has run_event broadcast"


def _assert_pre_recorded_payload(item: ProcessingStatusCall, entry: dict[str, Any]) -> None:
    broadcast = _find_prior_awaited_helper(item, "broadcast_run_event_payload")
    assert broadcast is not None, f"{entry['call_id']} missing run_event payload broadcast"
    assert _expr_equal(
        _call_expression(broadcast),
        entry["run_event_broadcast_expression"],
    )
    assert _expr_equal(_unparse(_keyword(broadcast, "sse")), entry["delivery_expression"])

    prior = _prior_statements(item)
    assert any(
        isinstance(stmt, ast.Assign)
        and any(_unparse(target) == entry["payload_variable"] for target in stmt.targets)
        and _expr_equal(
            _unparse(stmt.value.value if isinstance(stmt.value, ast.Await) else stmt.value),
            entry["pre_record_call_expression"],
        )
        for stmt in prior
    ), f"{entry['call_id']} missing pre-record assignment"
    assert any(
        isinstance(stmt, ast.If)
        and _expr_equal(_unparse(stmt.test), entry["payload_none_guard"].removeprefix("if ").removesuffix(": continue"))
        and any(isinstance(child, ast.Continue) for child in stmt.body)
        for stmt in prior
    ), f"{entry['call_id']} missing payload None continue guard"


def test_production_processing_status_callers_are_manifest_covered() -> None:
    manifest = _load_manifest()
    discovered = _discover_calls()

    missing_ids = [entry.get("call_id") for entry in manifest if not entry.get("call_id")]
    assert not missing_ids, "Every manifest entry must have a stable call_id"

    matched_call_ids: set[int] = set()
    errors: list[str] = []

    for entry in manifest:
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
        if call.line != entry.get("line"):
            errors.append(
                f"{entry['call_id']}: line is stale: manifest {entry.get('line')} "
                f"!= current {call.line}"
            )
            continue

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
        call=send_call,
        parents=parents,
    )

    assert _find_prior_awaited_helper(
        item, "record_and_maybe_broadcast_run_event"
    ) is None


def test_manifest_call_ids_do_not_encode_line_numbers() -> None:
    manifest = _load_manifest()
    line_suffixed = [
        entry["call_id"]
        for entry in manifest
        if entry.get("call_id", "").rsplit(".", 1)[-1].isdigit()
    ]
    assert not line_suffixed


def test_sse_manager_processing_status_has_no_run_lifecycle_side_effects() -> None:
    if os.environ.get("PHASE7A_ALLOW_LEGACY_SSE_MANAGER") == "1":
        return

    path = ROOT / "services" / "sse_services.py"
    tree = ast.parse(path.read_text(), filename="services/sse_services.py")
    forbidden_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "services.run_command_handler":
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
