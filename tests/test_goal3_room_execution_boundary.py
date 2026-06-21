import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _class_methods(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(), filename=path.as_posix())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            cls = node
            break
    else:
        raise AssertionError(f"{class_name} class not found in {path}")

    return {
        node.name
        for node in cls.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_room_services_has_no_execution_reverse_binding_surface():
    source_path = ROOT / "app_shell" / "room_runtime.py"
    source = source_path.read_text()
    methods = _class_methods(source_path, "RoomServices")

    forbidden_methods = {
        "bind_execution_event_deps",
        "bind_active_run_reader",
        "bind_hitl_pending_checker",
        "_send_processing_status",
        "_emit_processing_status_event",
        "_read_active_runs_for_room",
    }
    forbidden_snippets = {
        "_processing_status_emitter",
        "_active_run_reader",
        "_recovery_scheduler",
        "_hitl_pending_checker",
        "RoomActiveRunReader",
    }

    violating_methods = sorted(methods & forbidden_methods)
    assert not violating_methods, (
        "RoomServices still defines reverse binding methods: "
        f"{violating_methods}"
    )
    for snippet in forbidden_snippets:
        assert snippet not in source


def test_container_does_not_bind_execution_callables_into_room_runtime():
    tree = ast.parse((ROOT / "container.py").read_text(), filename="container.py")
    forbidden = {
        "bind_execution_event_deps",
        "bind_active_run_reader",
        "bind_hitl_pending_checker",
    }
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in forbidden:
            continue
        receiver = ast.unparse(node.func.value)
        if receiver == "room_runtime":
            violations.append(f"container.py:{node.lineno}: {receiver}.{node.func.attr}")

    assert not violations


def test_room_active_run_reader_protocol_was_removed():
    source = (ROOT / "common" / "protocols" / "execution_protocols.py").read_text()

    assert "RoomActiveRunReader" not in source
