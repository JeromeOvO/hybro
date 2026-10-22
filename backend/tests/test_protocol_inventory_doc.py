"""C7 exit: the architecture document lists every production Protocol."""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DOC = BACKEND / "docs" / "System-Architecture.md"
UNSCANNED = {".venv", "build", "tests", "scripts"}


def _is_protocol(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Protocol":
            return True
        if isinstance(base, ast.Subscript) and getattr(base.value, "id", "") == (
            "Protocol"
        ):
            return True
    return False


def _production_protocols() -> set[tuple[str, str]]:
    found = set()
    for path in BACKEND.rglob("*.py"):
        relative = path.relative_to(BACKEND)
        if relative.parts[0] in UNSCANNED or "__pycache__" in relative.parts:
            continue
        module = ".".join(relative.with_suffix("").parts).removesuffix(".__init__")
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.ClassDef) and _is_protocol(node):
                found.add((module, node.name))
    return found


def _documented_protocols() -> set[tuple[str, str]]:
    text = DOC.read_text()
    section = text.split("#### Protocol inventory", 1)[1].split("\n#", 1)[0]
    rows = re.findall(r"^\| `([^`]+)` \| `([^`]+)` \|", section, flags=re.M)
    return {(owner, name) for name, owner in rows}


def test_architecture_doc_lists_exactly_the_production_protocols():
    documented = _documented_protocols()
    actual = _production_protocols()

    assert actual - documented == set(), "undocumented Protocols"
    assert documented - actual == set(), "documented Protocols that no longer exist"
