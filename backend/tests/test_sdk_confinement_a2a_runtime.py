import ast
from pathlib import Path


def test_a2a_runtime_has_no_a2a_sdk_imports():
    tree = ast.parse(Path("a2a_adapter/runtime_service.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "a2a" or alias.name.startswith("a2a."):
                    raise AssertionError(f"Line {node.lineno}: still imports a2a SDK")
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "a2a" or node.module.startswith("a2a."))
        ):
            raise AssertionError(f"Line {node.lineno}: still imports a2a SDK")
