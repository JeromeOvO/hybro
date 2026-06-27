"""Final hard gate for deleted runtime-package cleanup."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REMOVED_RUNTIME_PACKAGE = "app_" + "shell"
SCOPE_NODE_TYPES = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef

RUNTIME_SCAN_ROOTS = (
    "main.py",
    "__main__.py",
    "container.py",
    "a2a_adapter",
    "api",
    "api_gateway",
    "agent",
    "common",
    "database",
    "room",
    "execution",
    "context_memory",
    "delivery",
    "hub_runtime_bridge",
    "llm_gateway",
    "platform_module",
    "dal",
    "jobs",
    "models",
    "scripts",
)
IGNORED_PARTS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}
DOC_REFERENCE_PATTERN = re.compile(
    rf"\b{re.escape(REMOVED_RUNTIME_PACKAGE)}\b"
    rf"|\b{re.escape('App' + 'Shell')}\b"
    r"|\bapp-shell\b"
    r"|\bapp shell\b",
    re.IGNORECASE,
)


def _runtime_python_files() -> list[Path]:
    files: list[Path] = []
    for root in RUNTIME_SCAN_ROOTS:
        root_path = ROOT / root
        if root_path.is_file() and root_path.suffix == ".py":
            files.append(root_path)
        elif root_path.is_dir():
            files.extend(
                path
                for path in root_path.rglob("*.py")
                if not (set(path.relative_to(ROOT).parts) & IGNORED_PARTS)
            )
    return sorted(files)


def _test_python_files() -> list[Path]:
    return sorted(
        path
        for path in (ROOT / "tests").rglob("*.py")
        if path != Path(__file__).resolve()
        and not (set(path.relative_to(ROOT).parts) & IGNORED_PARTS)
    )


def _is_removed_runtime_module(module: str) -> bool:
    return module == REMOVED_RUNTIME_PACKAGE or module.startswith(
        f"{REMOVED_RUNTIME_PACKAGE}."
    )


def _importlib_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    importlib_modules = {"importlib"}
    import_module_functions = {"import_module"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    import_module_functions.add(alias.asname or alias.name)

    return importlib_modules, import_module_functions


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Tuple | ast.List):
        names: set[str] = set()
        for item in node.elts:
            names.update(_target_names(item))
        return names
    return set()


def _constant_strings_from_body(
    body: list[ast.stmt],
    base_constants: dict[str, str] | None = None,
) -> dict[str, str]:
    constants = dict(base_constants or {})

    for node in body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value_node = node.value
        else:
            continue
        value = _literal_string(value_node, constants)
        if value is None:
            continue
        for target in targets:
            for name in _target_names(target):
                constants[name] = value

    return constants


def _literal_string(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string(node.left, constants)
        right = _literal_string(node.right, constants)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                formatted = _literal_string(value.value, constants)
                if formatted is None:
                    return None
                parts.append(formatted)
            else:
                return None
        return "".join(parts)
    return None


def _call_name(node: ast.AST, importlib_modules: set[str]) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "import_module"
        and isinstance(node.value, ast.Name)
        and node.value.id in importlib_modules
    ):
        return "importlib.import_module"
    return None


def _call_arg_or_kw(call: ast.Call, index: int, name: str) -> ast.AST | None:
    if len(call.args) > index:
        return call.args[index]
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _dynamic_import_module_name(
    *,
    call: ast.Call,
    supports_package: bool,
    constants: dict[str, str],
) -> str | None:
    name_node = _call_arg_or_kw(call, 0, "name")
    if name_node is None:
        return None
    module_name = _literal_string(name_node, constants)
    if module_name is None:
        return None

    if not supports_package:
        return module_name

    package_node = _call_arg_or_kw(call, 1, "package")
    package_name = (
        _literal_string(package_node, constants) if package_node is not None else None
    )
    if module_name.startswith("."):
        if package_name is not None and _is_removed_runtime_module(package_name):
            return f"{package_name}{module_name}"
        return None
    return module_name


def _walk_scope_nodes(body: list[ast.stmt]):
    stack = list(reversed(body))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _dynamic_import_violations_for_scope(
    *,
    rel_path: str,
    body: list[ast.stmt],
    constants: dict[str, str],
    importlib_modules: set[str],
    import_module_functions: set[str],
) -> list[str]:
    violations: list[str] = []
    for node in _walk_scope_nodes(body):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node.func, importlib_modules)
        if call_name == "__import__":
            module = _dynamic_import_module_name(
                call=node,
                supports_package=False,
                constants=constants,
            )
        elif (
            call_name == "importlib.import_module"
            or call_name in import_module_functions
        ):
            module = _dynamic_import_module_name(
                call=node,
                supports_package=True,
                constants=constants,
            )
        else:
            module = None
        if module is not None and _is_removed_runtime_module(module):
            violations.append(f"{rel_path}:{node.lineno}: {call_name}({module!r})")
    return violations


def _nested_scope_dynamic_import_violations(
    *,
    rel_path: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    parent_constants: dict[str, str],
    importlib_modules: set[str],
    import_module_functions: set[str],
) -> list[str]:
    constants = _constant_strings_from_body(node.body, parent_constants)
    violations = _dynamic_import_violations_for_scope(
        rel_path=rel_path,
        body=node.body,
        constants=constants,
        importlib_modules=importlib_modules,
        import_module_functions=import_module_functions,
    )
    for child in _walk_scope_nodes(node.body):
        if not isinstance(child, SCOPE_NODE_TYPES):
            continue
        violations.extend(
            _nested_scope_dynamic_import_violations(
                rel_path=rel_path,
                node=child,
                parent_constants=constants,
                importlib_modules=importlib_modules,
                import_module_functions=import_module_functions,
            )
        )
    return violations


def _has_scope_ancestor(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, SCOPE_NODE_TYPES):
            return True
        current = parents.get(current)
    return False


def _runtime_import_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    try:
        rel_path = path.relative_to(ROOT).as_posix()
    except ValueError:
        rel_path = path.as_posix()
    importlib_modules, import_module_functions = _importlib_aliases(tree)
    module_constants = _constant_strings_from_body(tree.body)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_removed_runtime_module(alias.name):
                    violations.append(f"{rel_path}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if _is_removed_runtime_module(node.module):
                violations.append(f"{rel_path}:{node.lineno}: from {node.module}")

    violations.extend(
        _dynamic_import_violations_for_scope(
            rel_path=rel_path,
            body=tree.body,
            constants=module_constants,
            importlib_modules=importlib_modules,
            import_module_functions=import_module_functions,
        )
    )
    for node in ast.walk(tree):
        if isinstance(node, SCOPE_NODE_TYPES) and not _has_scope_ancestor(
            node, parents
        ):
            violations.extend(
                _nested_scope_dynamic_import_violations(
                    rel_path=rel_path,
                    node=node,
                    parent_constants=module_constants,
                    importlib_modules=importlib_modules,
                    import_module_functions=import_module_functions,
                )
            )
    return violations


def test_removed_runtime_import_scanner_detects_class_importlib_constant(
    tmp_path: Path,
) -> None:
    sample = tmp_path / "class_importlib.py"
    sample.write_text(
        "\n".join(
            [
                "import importlib",
                "",
                "class Plugin:",
                "    base: str = 'app_' + 'shell'",
                "    plugin = importlib.import_module(f'{base}.foo')",
            ]
        )
    )

    assert _runtime_import_violations(sample) == [
        f"{sample.as_posix()}:5: importlib.import_module('app_shell.foo')"
    ]


def test_removed_runtime_import_scanner_detects_class_dunder_import_constant(
    tmp_path: Path,
) -> None:
    sample = tmp_path / "class_dunder_import.py"
    sample.write_text(
        "\n".join(
            [
                "REMOVED = 'app_' + 'shell'",
                "",
                "class Plugin:",
                "    leaf = '.bar'",
                "    plugin = __import__(REMOVED + leaf)",
            ]
        )
    )

    assert _runtime_import_violations(sample) == [
        f"{sample.as_posix()}:5: __import__('app_shell.bar')"
    ]


def test_removed_runtime_import_scanner_detects_importlib_keyword_name(
    tmp_path: Path,
) -> None:
    sample = tmp_path / "keyword_name.py"
    sample.write_text(
        "\n".join(
            [
                "import importlib",
                "REMOVED = 'app_' + 'shell'",
                "plugin = importlib.import_module(name=f'{REMOVED}.keyword')",
            ]
        )
    )

    assert _runtime_import_violations(sample) == [
        f"{sample.as_posix()}:3: importlib.import_module('app_shell.keyword')"
    ]


def test_removed_runtime_import_scanner_detects_importlib_relative_package(
    tmp_path: Path,
) -> None:
    sample = tmp_path / "relative_package.py"
    sample.write_text(
        "\n".join(
            [
                "import importlib",
                "REMOVED = 'app_' + 'shell'",
                "one = importlib.import_module('.positional', REMOVED)",
                "two = importlib.import_module('.keyword', package=REMOVED)",
            ]
        )
    )

    assert _runtime_import_violations(sample) == [
        f"{sample.as_posix()}:3: importlib.import_module('app_shell.positional')",
        f"{sample.as_posix()}:4: importlib.import_module('app_shell.keyword')",
    ]


def test_removed_runtime_import_scanner_detects_aliased_import_module_relative_package(
    tmp_path: Path,
) -> None:
    sample = tmp_path / "aliased_relative_package.py"
    sample.write_text(
        "\n".join(
            [
                "from importlib import import_module as im",
                "REMOVED = 'app_' + 'shell'",
                "plugin = im('.aliased', package=REMOVED)",
            ]
        )
    )

    assert _runtime_import_violations(sample) == [
        f"{sample.as_posix()}:3: im('app_shell.aliased')"
    ]


def _manifest_path_like_values(value):
    if isinstance(value, str):
        if "/" in value and REMOVED_RUNTIME_PACKAGE in value:
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _manifest_path_like_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _manifest_path_like_values(item)


def _route_inventory_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _route_inventory_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _route_inventory_values(item)


def test_removed_runtime_package_directory_is_absent() -> None:
    assert not (ROOT / REMOVED_RUNTIME_PACKAGE).exists()


def test_runtime_code_has_no_removed_runtime_package_imports() -> None:
    violations: list[str] = []
    for path in _runtime_python_files():
        violations.extend(_runtime_import_violations(path))

    assert not violations, (
        "Runtime code still imports the removed runtime package:\n"
        + "\n".join(violations)
    )


def test_tests_have_no_removed_runtime_package_imports() -> None:
    violations: list[str] = []
    for path in _test_python_files():
        violations.extend(_runtime_import_violations(path))

    assert not violations, (
        "Tests still import the removed runtime package:\n" + "\n".join(violations)
    )


def test_container_has_no_removed_runtime_package_references() -> None:
    source = (ROOT / "container.py").read_text()

    assert REMOVED_RUNTIME_PACKAGE not in source


def test_removed_runtime_package_is_not_shipped_or_lint_configured() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    packages = pyproject["tool"]["setuptools"]["packages"]
    ruff_per_file_ignores = pyproject["tool"]["ruff"]["lint"].get(
        "per-file-ignores", {}
    )
    violations: list[str] = []

    for package in packages:
        if _is_removed_runtime_module(package):
            violations.append(f"tool.setuptools.packages: {package}")
    for path in ruff_per_file_ignores:
        if path == REMOVED_RUNTIME_PACKAGE or path.startswith(
            f"{REMOVED_RUNTIME_PACKAGE}/"
        ):
            violations.append(f"tool.ruff.lint.per-file-ignores: {path}")

    assert not violations, (
        "Removed runtime package remains in packaging/lint metadata:\n"
        + "\n".join(violations)
    )


def test_phase9_cleanup_manifest_records_removed_runtime_package_final_state() -> None:
    manifest = json.loads(
        (ROOT / "tests" / "fixtures" / "phase9_cleanup_manifest.json").read_text()
    )
    runtime_blocker_key = f"{REMOVED_RUNTIME_PACKAGE}_runtime_blockers"
    checklist = manifest.get("package_removal_checklist") or []
    checklist_entry = next(
        (
            entry
            for entry in checklist
            if isinstance(entry, dict)
            and entry.get("package") == REMOVED_RUNTIME_PACKAGE
        ),
        None,
    )
    violations = list(_manifest_path_like_values(manifest))

    assert manifest.get("blocked_cleanup", []) == []
    assert manifest.get(runtime_blocker_key, []) == []
    assert not violations, (
        "Phase 9 cleanup manifest still contains removed runtime package paths:\n"
        + "\n".join(sorted(violations))
    )
    assert checklist_entry is not None
    assert checklist_entry.get("status") == "removed"
    assert checklist_entry.get("py_files") == 0
    assert checklist_entry.get("runtime_import_files") == []
    assert checklist_entry.get("test_import_files") == []
    assert checklist_entry.get("runtime_blockers") == []
    assert checklist_entry.get("test_blockers") == []
    assert checklist_entry.get("required_before_remove", []) == []
    assert REMOVED_RUNTIME_PACKAGE in set(manifest.get("safe_to_delete") or [])


def test_route_inventory_has_no_removed_runtime_protocol_owners() -> None:
    routes = json.loads(
        (ROOT / "tests" / "fixtures" / "phase9_api_routes.json").read_text()
    )
    violations = [
        value
        for value in _route_inventory_values(routes)
        if REMOVED_RUNTIME_PACKAGE in value
    ]

    assert not violations, (
        "Route inventory still references removed runtime package:\n"
        + "\n".join(sorted(violations))
    )


def test_docs_mark_removed_runtime_references_as_historical() -> None:
    violations: list[str] = []
    docs_root = ROOT / "docs"
    for path in sorted(docs_root.rglob("*.md")):
        rel_parts = path.relative_to(ROOT).parts
        if rel_parts[:2] == ("docs", "superpowers"):
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if DOC_REFERENCE_PATTERN.search(line) and "historical:" not in line:
                rel_path = path.relative_to(ROOT).as_posix()
                violations.append(f"{rel_path}:{lineno}: {line.strip()}")

    assert not violations, (
        "Docs references to the removed runtime package must use exact "
        "'historical:' marker:\n" + "\n".join(violations)
    )
