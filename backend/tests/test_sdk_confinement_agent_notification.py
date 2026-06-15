import ast
from pathlib import Path


def _assert_no_a2a_sdk_imports(path: str) -> None:
    tree = ast.parse(Path(path).read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "a2a" or node.module.startswith("a2a."))
        ):
            raise AssertionError(f"Line {node.lineno}: still imports a2a SDK")


def test_agent_service_has_no_a2a_sdk_imports():
    _assert_no_a2a_sdk_imports("app_shell/agent_service.py")


def test_notification_service_has_no_a2a_sdk_imports():
    _assert_no_a2a_sdk_imports("app_shell/notification_service.py")
