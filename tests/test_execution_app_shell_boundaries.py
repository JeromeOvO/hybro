import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_execution_modules_do_not_import_app_shell() -> None:
    bad: list[str] = []
    for path in sorted((ROOT / "execution").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel_path = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "app_shell" or node.module.startswith("app_shell."):
                    bad.append(f"{rel_path}:{node.lineno}:{node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app_shell" or alias.name.startswith(
                        "app_shell."
                    ):
                        bad.append(f"{rel_path}:{node.lineno}:{alias.name}")

    assert not bad, "Execution must depend on module-owned ports:\n" + "\n".join(bad)
