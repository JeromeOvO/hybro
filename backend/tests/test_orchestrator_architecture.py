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
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                package_parts = list(path.relative_to(ROOT).with_suffix("").parts[:-1])
                keep = max(0, len(package_parts) - node.level + 1)
                resolved = ".".join([*package_parts[:keep], module]).strip(".")
                modules.add(resolved)
            elif module:
                modules.add(module)
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
        "execution.orchestrator.a2a_runtime",
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


def test_generic_kernel_session_context_and_budget_have_no_plan3_or_product_dependencies():
    forbidden = {
        "a2a",
        "a2a_adapter",
        "room",
        "motor",
        "pymongo",
        "redis",
        "sse_starlette",
        "openai",
    }
    for filename in ("kernel.py", "session.py", "context.py", "budget.py"):
        modules = imported_modules(ORCHESTRATOR / filename)
        assert not {
            module
            for module in modules
            if module.startswith("execution.orchestrator.a2a_runtime")
            or any(
                module == root or module.startswith(f"{root}.") for root in forbidden
            )
        }, filename


def test_gateway_provider_inventory_is_closed_and_gemini_dependency_is_removed():
    providers = (ROOT / "llm_gateway" / "providers" / "__init__.py").read_text()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]
    assert "OpenAIProvider" in providers
    assert "DeepSeekProvider" in providers
    assert "GeminiProvider" not in providers
    assert not (ROOT / "llm_gateway" / "providers" / "gemini_provider.py").exists()
    assert not any(dependency.startswith("google-genai") for dependency in dependencies)


def test_profile_contracts_do_not_import_legacy_executors():
    source = (ORCHESTRATOR / "profiles.py").read_text()
    assert "QueueExecutor" not in source
    assert "SupervisorExecutor" not in source
    assert "queue_executor" not in source
    assert "supervisor_executor" not in source


def test_run_model_excludes_forbidden_semantic_state():
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


def test_orchestrator_runtime_composition_is_container_confined():
    """The container is the composition root; every other path stays dark.

    ``container.py`` may reference the full ``execution.orchestrator`` runtime
    (directly or through the dedicated ``orchestrator_composition``
    module). The legacy product entry points must stay orchestrator-free until
    dual routing lands in step 7.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])
    assert "execution.orchestrator" in packages
    assert "execution.orchestrator.a2a_runtime" in packages
    assert "dal.orchestrator" in packages

    container_modules = imported_modules(ROOT / "container.py")
    # The dedicated composition module must be the reach point from the
    # container; the real enforcement is the orchestrator-free entry-point
    # assertion below.
    assert "orchestrator_composition" in container_modules

    production_paths = [
        ROOT / "main.py",
        *(ROOT / "api_gateway").rglob("*.py"),
        *(ROOT / "jobs").rglob("*.py"),
        *(ROOT / "room").rglob("*.py"),
        ROOT / "execution" / "facade.py",
    ]
    bindings = [
        str(path.relative_to(ROOT))
        for path in production_paths
        if "execution.orchestrator" in path.read_text()
    ]
    assert bindings == []


def test_direct_client_boundary_contains_no_sdk_or_provider_types():
    dispatch = ORCHESTRATOR / "a2a_runtime" / "dispatch.py"
    modules = imported_modules(dispatch)
    forbidden = {"a2a", "a2a_adapter", "aiohttp", "httpx", "grpc"}
    assert not {
        module
        for module in modules
        if any(module == root or module.startswith(f"{root}.") for root in forbidden)
    }
    source = dispatch.read_text()
    assert "A2AClient" not in source.replace("DirectA2AClient", "")
    assert "ClientFactory" not in source


def test_persistence_metadata_is_unbound_and_has_no_repository_constructor():
    source = (ORCHESTRATOR / "persistence.py").read_text()
    assert "def __init__" not in source
    assert ".collection(" not in source
    assert "MongoClient" not in source
