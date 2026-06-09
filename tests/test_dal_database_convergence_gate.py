from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/fixtures/dal_database_convergence_manifest.json"
PRODUCTION_ROOTS = (
    "api",
    "api_gateway",
    "agent",
    "room",
    "context_memory",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "a2a_adapter",
    "platform_module",
    "llm_gateway",
    "app_shell",
    "jobs",
    "common",
    "container.py",
    "main.py",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imports_prefix(path: Path, prefix: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for module in _imports(path)
    )


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def _py_files() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCTION_ROOTS:
        path = ROOT / root
        if not path.exists():
            continue
        files.extend([path] if path.is_file() else sorted(path.rglob("*.py")))
    return sorted(set(files))


def _violating_files(prefix: str) -> list[str]:
    found: list[str] = []
    for path in _py_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("database/"):
            continue
        if _imports_prefix(path, prefix):
            found.append(rel)
    return sorted(found)


def _has_hidden_mongo_fallback(path: Path) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "mongodb"
                for target in node.targets
            )
        ):
            return True
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "mongodb"
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "hasattr"}
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "mongodb"
        ):
            return True
    return False


def _has_database_service_duck_usage(path: Path) -> bool:
    source = path.read_text()
    if (
        "database_service" not in source
        and "db_service" not in source
        and "DatabaseService" not in source
    ):
        return False
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                if arg.arg in {"database_service", "db_service"}:
                    return True
        if isinstance(node, ast.Assign):
            target_names = {
                target.attr if isinstance(target, ast.Attribute) else target.id
                for target in node.targets
                if isinstance(target, (ast.Attribute, ast.Name))
            }
            if target_names.intersection({"_db", "_database_service", "_db_service"}):
                if isinstance(node.value, ast.Name) and node.value.id in {
                    "database_service",
                    "db_service",
                }:
                    return True

    forbidden_snippets = (
        "self.database_service",
        "self._database_service",
        "self._db_service",
        "self._db = db_service",
        "database_service=",
        "db_service=",
        "DatabaseHITLPersistenceAdapter",
        "_DatabaseServiceLike",
        "\"database_service\":",
        "\"db_service\":",
        "'database_service':",
        "'db_service':",
        "db_service:",
        "db_service =",
        "global db_service",
        "(\"db_service\"",
        "('db_service'",
    )
    return any(snippet in source for snippet in forbidden_snippets)


def test_database_mongodb_import_blockers_are_exact():
    expected = sorted(_manifest()["database_singleton_import_blockers"])
    assert _violating_files("database.mongodb") == expected


def test_hidden_mongo_fallback_blockers_are_exact():
    expected = sorted(_manifest()["hidden_mongo_fallback_blockers"])
    found = [
        path.relative_to(ROOT).as_posix()
        for path in _py_files()
        if _has_hidden_mongo_fallback(path)
    ]
    assert sorted(found) == expected


def test_database_pinecone_import_blockers_are_exact():
    expected = sorted(_manifest()["pinecone_singleton_import_blockers"])
    assert _violating_files("database.pinecone_db") == expected


def test_database_service_type_blockers_are_exact():
    expected = sorted(_manifest()["database_service_type_blockers"])
    found: list[str] = []
    for path in _py_files():
        rel = path.relative_to(ROOT).as_posix()
        if _imports_prefix(path, "app_shell.database_service"):
            found.append(rel)
    assert sorted(found) == expected


def test_database_service_duck_type_blockers_are_exact():
    expected = sorted(_manifest()["database_service_duck_type_blockers"])
    found = [
        path.relative_to(ROOT).as_posix()
        for path in _py_files()
        if _has_database_service_duck_usage(path)
    ]
    assert sorted(found) == expected
