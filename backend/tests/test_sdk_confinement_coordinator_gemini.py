import ast
from pathlib import Path


def _assert_no_a2a_sdk_imports(path: str) -> None:
    tree = ast.parse(Path(path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "a2a" or alias.name.startswith("a2a."):
                    raise AssertionError(f"Line {node.lineno}: still imports a2a SDK")
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "a2a" or node.module.startswith("a2a."))
        ):
            raise AssertionError(f"Line {node.lineno}: still imports a2a SDK")


def test_synthesis_coordinator_has_no_a2a_sdk_imports():
    _assert_no_a2a_sdk_imports("execution/orchestration/synthesis_coordinator.py")
