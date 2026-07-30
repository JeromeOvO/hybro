import ast
from pathlib import Path


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
            and node.name == name
        ):
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found")


def test_supervisor_executor_has_no_run_v2_business_path():
    source = Path("execution/orchestration/supervisor_executor.py").read_text()

    assert "async def run_v2(" not in source
    assert "return await self.run_v2(" not in source
    assert "self.run_v2(" not in source


def test_execute_orchestration_loop_has_no_trajectory_core_state():
    source = Path("execution/orchestration/supervisor_executor.py").read_text()
    loop_source = _function_source(source, "_execute_orchestration_loop")

    assert "SupervisorTrajectory()" not in loop_source
    assert "_resume_trajectory_for_state_loop" not in loop_source
    assert "trajectory=trajectory" not in loop_source
    assert "self._checkpoint_trajectory(" not in loop_source
    assert "async def _checkpoint_trajectory(" not in source


def test_room_message_center_does_not_route_to_run_v2():
    source = Path("execution/orchestration/room_message_center.py").read_text()

    assert "self.supervisor_executor.run_v2" not in source
    assert "if is_v2_supervisor" not in source
    assert "if is_v2_resume" not in source


def _assignment_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, ast.AnnAssign | ast.AugAssign):
        return [node.target]
    return []


def _is_supervisor_trajectory_key_assignment(target: ast.AST) -> bool:
    if not isinstance(target, ast.Subscript):
        return False
    slice_node = target.slice
    if isinstance(slice_node, ast.Constant):
        return slice_node.value == "supervisor_trajectory"
    return False


def test_new_execution_files_do_not_assign_supervisor_trajectory():
    checked = [
        Path("execution/orchestration/supervisor_executor.py"),
        Path("execution/orchestration/room_message_center.py"),
        Path("room/compat/runtime.py"),
    ]
    offenders: list[str] = []
    for path in checked:
        source = path.read_text()
        tree = ast.parse(source)
        lines = source.splitlines()
        for node in ast.walk(tree):
            for target in _assignment_targets(node):
                if _is_supervisor_trajectory_key_assignment(target):
                    offenders.append(
                        f"{path}:{node.lineno}:{lines[node.lineno - 1].strip()}"
                    )

    assert offenders == []


def test_frontend_does_not_read_supervisor_trajectory_directly():
    frontend_root = Path(__file__).resolve().parents[2] / "frontend"
    assert frontend_root.is_dir(), f"frontend checkout not found: {frontend_root}"
    ignored_parts = {"node_modules", ".next", "dist", "build", "coverage"}
    matches: list[str] = []
    for path in frontend_root.rglob("*"):
        if not path.is_file() or ignored_parts.intersection(path.parts):
            continue
        if path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        text = path.read_text(errors="ignore")
        if "supervisor_trajectory" in text or "SupervisorTrajectory" in text:
            matches.append(str(path))

    assert matches == []
