import ast
from pathlib import Path


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
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


def test_room_message_center_does_not_route_to_run_v2():
    source = Path("execution/orchestration/room_message_center.py").read_text()

    assert "self.supervisor_executor.run_v2" not in source
    assert "if is_v2_supervisor" not in source
    assert "if is_v2_resume" not in source
