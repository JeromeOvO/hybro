from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from execution.orchestrator.models import OrchestratorRunState

ROOT = Path(__file__).parents[1]
ORCHESTRATOR = ROOT / "execution" / "orchestrator"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_orchestrator_contracts_do_not_import_runtime_adapters_or_old_policies():
    forbidden_roots = {
        "fastapi",
        "motor",
        "pymongo",
        "redis",
        "sse_starlette",
        "google",
        "openai",
        "execution.orchestration",
    }
    violations = []
    for path in ORCHESTRATOR.glob("*.py"):
        for module in imported_modules(path):
            if any(
                module == root or module.startswith(f"{root}.")
                for root in forbidden_roots
            ):
                violations.append(f"{path.relative_to(ROOT)} imports {module}")
    assert violations == []


def test_llm_gateway_does_not_import_execution_or_room_models():
    violations = []
    for path in (ROOT / "llm_gateway").rglob("*.py"):
        for module in imported_modules(path):
            if module == "execution" or module.startswith("execution."):
                violations.append(f"{path.relative_to(ROOT)} imports {module}")
            if module == "room" or module.startswith("room."):
                violations.append(f"{path.relative_to(ROOT)} imports {module}")
    assert violations == []


def test_a2a_adapter_does_not_import_orchestrator_policy():
    violations = []
    for path in (ROOT / "a2a_adapter").rglob("*.py"):
        for module in imported_modules(path):
            if module.startswith("execution.orchestrator") or module.startswith(
                "execution.orchestration"
            ):
                violations.append(f"{path.relative_to(ROOT)} imports {module}")
    assert violations == []


def test_profile_contracts_do_not_import_legacy_executors():
    source = (ORCHESTRATOR / "profiles.py").read_text()
    assert "QueueExecutor" not in source
    assert "SupervisorExecutor" not in source
    assert "queue_executor" not in source
    assert "supervisor_executor" not in source


def test_v3_run_model_excludes_forbidden_semantic_state():
    forbidden = {
        "goal_families",
        "goal_family",
        "goal_revision",
        "required_obligations",
        "remaining_required_obligations",
        "completion_evidence",
        "completion_waivers",
        "semantic_blockers",
        "planner_validation_failures",
        "semantic_retry_fingerprints",
        "epochs",
        "disposition_lineage",
        "required_fields",
        "goal_fingerprint",
        "result_fingerprint",
    }
    models = [
        OrchestratorRunState,
        *[
            field.annotation
            for field in OrchestratorRunState.model_fields.values()
            if isinstance(field.annotation, type)
            and hasattr(field.annotation, "model_fields")
        ],
    ]
    fields = {
        field_name
        for model in models
        for field_name in getattr(model, "model_fields", {})
    }
    source = "\n".join(path.read_text() for path in ORCHESTRATOR.glob("*.py"))

    assert fields.isdisjoint(forbidden)
    for name in forbidden:
        assert f"{name}:" not in source


def test_v3_package_is_distributable_but_not_bound_to_production():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])
    assert "execution.orchestrator" in packages

    production_paths = [
        ROOT / "container.py",
        ROOT / "main.py",
        *(ROOT / "api_gateway").rglob("*.py"),
        *(ROOT / "jobs").rglob("*.py"),
    ]
    bindings = [
        str(path.relative_to(ROOT))
        for path in production_paths
        if "execution.orchestrator" in path.read_text()
    ]
    assert bindings == []

    container_source = (ROOT / "container.py").read_text()
    assert "execution.orchestration.room_message_center" in container_source
    assert "execution.orchestration.factory" in container_source


def test_v3_persistence_metadata_is_unbound_and_has_no_repository_constructor():
    source = (ORCHESTRATOR / "persistence.py").read_text()
    assert "def __init__" not in source
    assert ".collection(" not in source
    assert "MongoClient" not in source
