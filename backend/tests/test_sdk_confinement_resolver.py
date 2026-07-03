import ast
from pathlib import Path


def test_agent_resolver_service_has_no_a2a_sdk_imports():
    tree = ast.parse(Path("agent/resolver.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "a2a" or node.module.startswith("a2a."))
        ):
            raise AssertionError(f"Line {node.lineno}: still imports a2a SDK")


def test_agent_resolver_service_uses_adapter_for_network_io():
    source = Path("agent/resolver.py").read_text()

    assert "httpx.AsyncClient" not in source
    assert "AGENT_CARD_WELL_KNOWN_PATH" not in source
    assert "PREV_AGENT_CARD_WELL_KNOWN_PATH" not in source
    assert "probe_agent_card_for_health" in source
