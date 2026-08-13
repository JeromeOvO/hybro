import ast
import inspect
import textwrap
from pathlib import Path
from typing import get_type_hints


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_room_services_process_agent_message_is_thin_delegate():
    from room.compat.runtime import RoomServices

    source = inspect.getsource(RoomServices.process_agent_message)
    tree = ast.parse(textwrap.dedent(source))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

    assert "_require_agent_message_preparation" in source
    assert "process_agent_message" in source
    assert len(calls) == 2
    assert "get_agent_by_agent_id" not in source
    assert "load_turn_context" not in source
    assert "AttachmentPreflightFailure" not in source


def test_agent_message_preparation_has_only_narrow_dependencies():
    from common.protocols import AttachmentContentReader
    from room.agent_message_preparation import AgentMessagePreparationService

    source = inspect.getsource(AgentMessagePreparationService)
    type_hints = get_type_hints(AgentMessagePreparationService.__init__)
    assert "RoomServices" not in source
    assert "self._store" not in source
    assert "runtime_store" not in source
    assert "room.compat" not in Path("room/agent_message_preparation.py").read_text()
    assert type_hints["attachment_content_reader"] is AttachmentContentReader


def test_container_wires_explicit_preparation_readers():
    source = Path("container.py").read_text()

    assert "AgentMessagePreparationService(" in source
    assert "user_message_reader=SimpleNamespace(" in source
    assert "quote_reader=SimpleNamespace(" in source
    assert "message_lineage_reader=SimpleNamespace(" in source
    assert "attachment_content_reader=SimpleNamespace(" in source
    assert "room_runtime.bind_agent_message_preparation(" in source
    assert "get_room_user_messages_by_room_id=(" in source
    assert "get_quoted_snippet_by_id=get_quoted_snippet_by_id" in source


def test_room_services_syncs_context_assembly_for_either_binding_order():
    from unittest.mock import MagicMock

    from room.compat.runtime import RoomServices

    context_assembly = object()
    preparation = MagicMock()
    services = RoomServices()
    services.bind_context_memory(context_assembly=context_assembly)
    services.bind_agent_message_preparation(preparation)
    preparation.bind_context_assembly.assert_called_once_with(context_assembly)

    preparation.reset_mock()
    services = RoomServices()
    services.bind_agent_message_preparation(preparation)
    services.bind_context_memory(context_assembly=context_assembly)
    preparation.bind_context_assembly.assert_called_once_with(context_assembly)


def test_execution_does_not_import_room_compat_runtime():
    violations: list[str] = []
    for path in Path("execution").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "room.compat.runtime"
            ):
                violations.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "room.compat.runtime":
                        violations.append(f"{path}:{node.lineno}")
    assert violations == []


def test_production_package_import_graph_has_no_cycles():
    package_roots = {
        path.name
        for path in Path(".").iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    }
    graph = {root: set() for root in package_roots}
    for root in package_roots:
        for path in (Path(root)).rglob("*.py"):
            graph[root].update(_import_roots(path) & package_roots - {root})

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, trail: tuple[str, ...]) -> None:
        if node in visiting:
            raise AssertionError("package import cycle: " + " -> ".join((*trail, node)))
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency, (*trail, node))
        visiting.remove(node)
        visited.add(node)

    for root in graph:
        visit(root, ())
