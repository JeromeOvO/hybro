import ast
import json
from pathlib import Path

APP_SHELL_TARGETS = {
    "app_shell/room_runtime.py",
    "app_shell/a2a_runtime.py",
    "app_shell/relay_service.py",
    "app_shell/context_assembly_service.py",
    "app_shell/repository_store.py",
}

FORBIDDEN_APP_SHELL_IMPORT_PREFIXES = (
    "a2a",
    "aioboto3",
    "botocore",
    "common.config.settings",
    "database.mongodb",
)

EXPECTED_APP_SHELL_BASELINE = {
    "app_shell/room_runtime.py": {"lines": 3848, "public_business_methods": 53},
    "app_shell/a2a_runtime.py": {"lines": 613, "public_business_methods": 16},
    "app_shell/relay_service.py": {"lines": 862, "public_business_methods": 27},
    "app_shell/context_assembly_service.py": {
        "lines": 164,
        "public_business_methods": 4,
    },
    "app_shell/repository_store.py": {"lines": 759, "public_business_methods": 93},
}


def _manifest() -> dict:
    return json.loads(Path("tests/fixtures/phase9_cleanup_manifest.json").read_text())


def _forbidden_prefix(module: str) -> str | None:
    for prefix in FORBIDDEN_APP_SHELL_IMPORT_PREFIXES:
        if module == prefix or module.startswith(f"{prefix}."):
            return prefix
    return None


def _legacy_import_blockers() -> set[tuple[str, str]]:
    blockers: set[tuple[str, str]] = set()
    for entry in _manifest().get("blocked_cleanup", []):
        if entry.get("contract") != "legacy_import_boundary":
            continue
        path = entry.get("path")
        prefix = entry.get("forbidden_prefix")
        if isinstance(path, str) and isinstance(prefix, str):
            blockers.add((path, prefix))
    return blockers


def _import_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append((node.lineno, node.module))
    return modules


def _is_property_like(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "property":
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr in {
            "setter",
            "deleter",
        }:
            return True
    return False


def _public_business_method_count(path: Path) -> int:
    tree = ast.parse(path.read_text(), filename=str(path))
    count = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                count += 1
            continue
        if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name.startswith("_") or _is_property_like(item):
                continue
            count += 1
    return count


def test_forbidden_prefix_matching_is_segment_aware():
    assert _forbidden_prefix("a2a") == "a2a"
    assert _forbidden_prefix("a2a.client") == "a2a"
    assert _forbidden_prefix("a2a_adapter.client_facade") is None


def test_app_shell_forbidden_imports_are_manifest_blocked_by_exact_prefix():
    blockers = _legacy_import_blockers()
    violations: list[str] = []

    for target in sorted(APP_SHELL_TARGETS):
        path = Path(target)
        for lineno, module in _import_modules(path):
            prefix = _forbidden_prefix(module)
            if prefix is None:
                continue
            if (target, prefix) in blockers:
                continue
            violations.append(f"{target}:{lineno}: {module}")

    assert not violations, "Forbidden app-shell imports remain:\n" + "\n".join(
        violations
    )


def test_legacy_import_boundary_blockers_are_exact_current_files():
    blockers = _legacy_import_blockers()
    bad: list[str] = []

    for target, prefix in sorted(blockers):
        if target not in APP_SHELL_TARGETS:
            continue
        path = Path(target)
        if not any(
            module == prefix or module.startswith(f"{prefix}.")
            for _, module in _import_modules(path)
        ):
            bad.append(f"{target}: missing live import for {prefix}")

    assert not bad, "App-shell thinning blockers are stale:\n" + "\n".join(bad)


def test_app_shell_focus_file_baseline_sizes_are_recorded():
    actual = {
        target: {
            "lines": sum(1 for _ in Path(target).open()),
            "public_business_methods": _public_business_method_count(Path(target)),
        }
        for target in sorted(APP_SHELL_TARGETS)
    }

    assert actual == EXPECTED_APP_SHELL_BASELINE


def test_context_memory_runtime_wiring_avoids_app_shell_singletons():
    forbidden = {
        "app_shell.context_assembly_service",
        "app_shell.memory_search_service",
    }
    targets = [
        Path("app_shell/room_runtime.py"),
        Path("execution/orchestration/room_message_center.py"),
        Path("execution/orchestration/factory.py"),
        Path("main.py"),
    ]
    violations: list[str] = []

    for path in targets:
        for lineno, module in _import_modules(path):
            if module in forbidden:
                violations.append(f"{path}:{lineno}: {module}")

    assert not violations, "App-shell context singleton imports remain:\n" + "\n".join(
        violations
    )
