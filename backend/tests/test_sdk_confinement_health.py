import ast
from pathlib import Path


def test_agent_health_service_has_no_a2a_sdk_imports():
    tree = ast.parse(Path("agent/health.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "a2a" or node.module.startswith("a2a."))
        ):
            raise AssertionError(f"Line {node.lineno}: still imports a2a SDK")


def test_agent_health_service_uses_adapter_for_network_io():
    source = Path("agent/health.py").read_text()

    assert "httpx.AsyncClient" not in source
    assert "fetch_agent_card_for_health" not in source
    assert "probe_agent_card_for_health" in source
