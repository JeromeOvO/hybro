"""AST gate: no os.getenv / os.environ reads outside common/config/settings.py.

Design ref: docs/MODULAR_DECOUPLING_DESIGN.md Section 7.1 (Phase 0b gate)
Gate criterion: tracked runtime Python files have no raw env reads except
common/config/settings.py.
"""

from __future__ import annotations

import ast
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "config_unification_manifest.json"


@dataclass(frozen=True)
class EnvVarViolation:
    path: str
    line: int
    col: int
    call: str


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def _is_excluded(rel_path: str, excluded_dirs: set[str]) -> bool:
    parts = rel_path.split("/")
    for i in range(len(parts)):
        prefix = "/".join(parts[: i + 1])
        if prefix in excluded_dirs:
            return True
    return False


def _is_allowed(rel_path: str, allowed_paths: set[str]) -> bool:
    return rel_path in allowed_paths


def _os_import_aliases(tree: ast.AST) -> tuple[set[str], set[str], set[str]]:
    """Return names bound to os, os.getenv, and os.environ in this module."""
    os_names = {"os"}
    getenv_names: set[str] = set()
    environ_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                bound = alias.asname or alias.name
                if alias.name == "getenv":
                    getenv_names.add(bound)
                elif alias.name == "environ":
                    environ_names.add(bound)

    return os_names, getenv_names, environ_names


def _scan_file(path: Path) -> list[EnvVarViolation]:
    """Scan for direct or alias-based os env reads."""
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return []

    violations: list[EnvVarViolation] = []
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:
        rel = path.name
    os_names, getenv_names, environ_names = _os_import_aliases(tree)

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in os_names
        ):
            violations.append(
                EnvVarViolation(rel, node.lineno, node.col_offset, "os.getenv()")
            )

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in getenv_names
        ):
            violations.append(
                EnvVarViolation(rel, node.lineno, node.col_offset, "getenv()")
            )

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id in os_names
        ):
            violations.append(
                EnvVarViolation(rel, node.lineno, node.col_offset, "os.environ.get()")
            )

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in environ_names
        ):
            violations.append(
                EnvVarViolation(rel, node.lineno, node.col_offset, "environ.get()")
            )

        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id in os_names
        ):
            violations.append(
                EnvVarViolation(rel, node.lineno, node.col_offset, "os.environ[...]")
            )

        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in environ_names
        ):
            violations.append(
                EnvVarViolation(rel, node.lineno, node.col_offset, "environ[...]")
            )

    return violations


def _collect_production_files() -> list[Path]:
    manifest = _manifest()
    excluded_dirs = set(manifest.get("excluded_dirs", []))
    allowed_paths = set(manifest.get("allowed_paths", []))

    result = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    files: list[Path] = []
    for rel in result.stdout.splitlines():
        if _is_excluded(rel, excluded_dirs):
            continue
        if _is_allowed(rel, allowed_paths):
            continue
        path = ROOT / rel
        if path.exists():
            files.append(path)

    return files


def test_no_raw_env_reads_in_production_code():
    """Phase 0b gate: runtime code must not read env vars directly."""
    files = _collect_production_files()
    all_violations: list[EnvVarViolation] = []

    for file in files:
        all_violations.extend(_scan_file(file))

    if all_violations:
        report = "\n".join(
            f"  {v.path}:{v.line}:{v.col} - {v.call}"
            for v in sorted(all_violations, key=lambda v: (v.path, v.line))
        )
        pytest.fail(
            f"Config unification gate FAILED: {len(all_violations)} raw env "
            f"var read(s) found outside common/config/settings.py:\n{report}\n\n"
            f"Fix: migrate to a Settings field in common/config/settings.py"
        )


def test_manifest_allowed_paths_exist():
    """Ensure manifest doesn't reference stale paths."""
    manifest = _manifest()
    for path in manifest.get("allowed_paths", []):
        assert (ROOT / path).exists(), f"Allowed path does not exist: {path}"


@pytest.mark.parametrize(
    ("source", "call"),
    [
        ("import os\nvalue = os.getenv('FOO')\n", "os.getenv()"),
        (
            "import os as operating_system\nvalue = operating_system.getenv('FOO')\n",
            "os.getenv()",
        ),
        ("from os import getenv\nvalue = getenv('FOO')\n", "getenv()"),
        ("import os\nvalue = os.environ.get('FOO')\n", "os.environ.get()"),
        ("from os import environ\nvalue = environ.get('FOO')\n", "environ.get()"),
        ("import os\nvalue = os.environ['FOO']\n", "os.environ[...]"),
        ("from os import environ\nvalue = environ['FOO']\n", "environ[...]"),
    ],
)
def test_scan_file_detects_direct_and_alias_env_reads(
    tmp_path: Path,
    source: str,
    call: str,
):
    path = tmp_path / "snippet.py"
    path.write_text(source)

    assert _scan_file(path) == [EnvVarViolation("snippet.py", 2, 8, call)]
