import ast
from pathlib import Path


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_execution_does_not_import_room_compat_runtime():
    violations: list[str] = []
    for path in Path("execution").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "conversation.runtime"
            ):
                violations.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "conversation.runtime":
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
