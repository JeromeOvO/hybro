import ast
from pathlib import Path


def test_room_runtime_has_no_a2a_sdk_imports():
    tree = ast.parse(Path("room/compat/runtime.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "a2a" or node.module.startswith("a2a."))
        ):
            raise AssertionError(f"Line {node.lineno}: still imports a2a SDK")
