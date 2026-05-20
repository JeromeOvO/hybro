import ast
import json
from pathlib import Path

import tomllib


PRODUCTION_ROOTS = (
    "api",
    "agent",
    "room",
    "context_memory",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "a2a_adapter",
    "llm_gateway",
    "platform_module",
    "common",
    "jobs",
    "models",
)

FORBIDDEN_PRODUCTION_IMPORT_PREFIXES = (
    "services",
    "modules",
    "database.mongodb",
    "config.settings",
)

LEGACY_PACKAGES = {"modules", "services", "config", "infrastructure"}

FORBIDDEN_LEGACY_SHIM_IMPORT_PREFIXES = (
    "a2a",
    "database.mongodb",
    "config.settings",
    "services",
)

FORBIDDEN_LEGACY_SHIM_GLOBALS = {"mongodb", "settings", "s3_service"}
FORBIDDEN_LEGACY_SHIM_CLASS_PREFIXES = ("_Legacy", "_Mongo")

FORBIDDEN_COMMON_IMPORT_PREFIXES = (
    "database",
    "services",
    "modules",
    "config",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "models",
    "platform_module",
    "a2a_adapter",
)

SDK_CONFINEMENT_ROOTS = (
    "main.py",
    "container.py",
    "api",
    "agent",
    "room",
    "context_memory",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "jobs",
    "models",
    "platform_module",
    "common",
)

FORBIDDEN_SDK_IMPORT_PREFIXES = ("a2a",)


def _manifest() -> dict:
    return json.loads(Path("tests/fixtures/phase9_cleanup_manifest.json").read_text())


def _blocked_cleanup_paths(*, contract: str | None = None) -> set[str]:
    paths: set[str] = set()
    for entry in _manifest().get("blocked_cleanup", []):
        if contract is not None and entry.get("contract") != contract:
            continue
        path = entry.get("path")
        if isinstance(path, str):
            paths.add(path)
    return paths


def _is_blocked(path: Path, blocked_paths: set[str]) -> bool:
    rel = path.as_posix()
    return any(
        rel == blocked or rel.startswith(f"{blocked.rstrip('/')}/")
        for blocked in blocked_paths
    )


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_PRODUCTION_IMPORT_PREFIXES
    )


def _production_python_files() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCTION_ROOTS:
        root_path = Path(root)
        if root_path.exists():
            files.extend(root_path.rglob("*.py"))
    return sorted(files)


def _import_violations() -> list[str]:
    violations: list[str] = []
    blocked_paths = _blocked_cleanup_paths(contract="legacy_import_boundary")
    for path in _production_python_files():
        if path == Path("common/config/settings.py"):
            continue
        if _is_blocked(path, blocked_paths):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [(alias.name, alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [(f"{node.module}.{alias.name}", node.module) for alias in node.names]
            else:
                continue
            for imported_name, module in names:
                if _is_forbidden(module):
                    violations.append(f"{path}:{node.lineno}: {imported_name}")
    return violations


def _sdk_import_violations() -> list[str]:
    violations: list[str] = []
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")
    for root in SDK_CONFINEMENT_ROOTS:
        root_path = Path(root)
        if not root_path.exists():
            continue
        paths = [root_path] if root_path.is_file() else sorted(root_path.rglob("*.py"))
        for path in paths:
            if _is_blocked(path, blocked_paths):
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [(alias.name, alias.name) for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    names = [(f"{node.module}.{alias.name}", node.module) for alias in node.names]
                else:
                    continue
                for imported_name, module in names:
                    if any(
                        module == prefix or module.startswith(f"{prefix}.")
                        for prefix in FORBIDDEN_SDK_IMPORT_PREFIXES
                    ):
                        violations.append(f"{path}:{node.lineno}: {imported_name}")
    return violations


def _sdk_import_files() -> set[str]:
    files: set[str] = set()
    for root in SDK_CONFINEMENT_ROOTS:
        root_path = Path(root)
        if not root_path.exists():
            continue
        paths = [root_path] if root_path.is_file() else sorted(root_path.rglob("*.py"))
        for path in paths:
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    modules = [node.module]
                else:
                    continue
                if any(
                    module == prefix or module.startswith(f"{prefix}.")
                    for module in modules
                    for prefix in FORBIDDEN_SDK_IMPORT_PREFIXES
                ):
                    files.add(path.as_posix())
                    break
    return files


def _common_import_violations() -> list[str]:
    violations: list[str] = []
    blocked_paths = _blocked_cleanup_paths(contract="common_import_boundary")
    for path in sorted(Path("common").rglob("*.py")):
        if path == Path("common/config/settings.py"):
            continue
        if _is_blocked(path, blocked_paths):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [(alias.name, alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [(f"{node.module}.{alias.name}", node.module) for alias in node.names]
            else:
                continue
            for imported_name, module in names:
                if any(
                    module == prefix or module.startswith(f"{prefix}.")
                    for prefix in FORBIDDEN_COMMON_IMPORT_PREFIXES
                ):
                    violations.append(f"{path}:{node.lineno}: {imported_name}")
    return violations


def _legacy_service_shim_paths() -> list[Path]:
    return sorted(
        Path(entry["path"])
        for entry in _manifest().get("blocked_cleanup", [])
        if isinstance(entry.get("path"), str)
        and entry["path"].startswith("services/")
        and entry["path"].endswith(".py")
    )


def _legacy_service_shim_violations() -> list[str]:
    violations: list[str] = []
    for path in _legacy_service_shim_paths():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [(alias.name, alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [(f"{node.module}.{alias.name}", node.module) for alias in node.names]
            else:
                continue
            for imported_name, module in names:
                if any(
                    module == prefix or module.startswith(f"{prefix}.")
                    for prefix in FORBIDDEN_LEGACY_SHIM_IMPORT_PREFIXES
                ):
                    violations.append(f"{path}:{node.lineno}: {imported_name}")

        source = path.read_text()
        if "_require_delegate" not in source:
            violations.append(f"{path}: missing fail-fast delegate boundary")
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id in FORBIDDEN_LEGACY_SHIM_GLOBALS
                    ):
                        violations.append(f"{path}:{node.lineno}: {target.id}")
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in FORBIDDEN_LEGACY_SHIM_GLOBALS:
                    violations.append(f"{path}:{node.lineno}: {node.target.id}")
            if isinstance(node, ast.ClassDef) and node.name.startswith(
                FORBIDDEN_LEGACY_SHIM_CLASS_PREFIXES
            ):
                violations.append(f"{path}:{node.lineno}: {node.name}")
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if (
                        isinstance(item, ast.FunctionDef)
                        and item.name == "collection"
                        and any(
                            isinstance(decorator, ast.Name)
                            and decorator.id == "property"
                            for decorator in item.decorator_list
                        )
                    ):
                        violations.append(f"{path}:{item.lineno}: collection")
    return violations


def test_no_production_imports_from_legacy_singletons():
    violations = _import_violations()

    assert not violations, "Legacy production imports remain:\n" + "\n".join(violations)


def test_a2a_sdk_imports_are_confined_or_manifest_blocked():
    violations = _sdk_import_violations()

    assert not violations, "Undocumented A2A SDK imports remain:\n" + "\n".join(violations)


def test_a2a_sdk_blockers_are_exact_current_files():
    blocked = _blocked_cleanup_paths(contract="sdk_confinement")
    imported = _sdk_import_files()

    assert blocked == imported


def test_hub_a2a_card_adapter_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "hub_runtime_bridge/adapters/a2a_card.py" not in blocked_paths


def test_hub_legacy_failure_adapter_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "hub_runtime_bridge/adapters/legacy_failure.py" not in blocked_paths


def test_gateway_models_have_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "models/gateway.py" not in blocked_paths


def test_processing_models_have_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "models/processing.py" not in blocked_paths


def test_task_models_have_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "models/task.py" not in blocked_paths


def test_main_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "main.py" not in blocked_paths


def test_common_a2a_client_has_no_sdk_confinement_blockers():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "common/client/card_resolver.py" not in blocked_paths
    assert "common/client/client.py" not in blocked_paths


def test_legacy_public_models_have_no_sdk_confinement_blockers():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "models/agent.py" not in blocked_paths
    assert "models/request.py" not in blocked_paths
    assert "models/response.py" not in blocked_paths
    assert "models/room.py" not in blocked_paths


def test_execution_dispatch_middleware_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "execution/dispatch/dispatch_middleware.py" not in blocked_paths


def test_execution_response_handler_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "execution/dispatch/response_handler.py" not in blocked_paths


def test_execution_queue_executor_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "execution/orchestration/queue_executor.py" not in blocked_paths


def test_execution_task_state_manager_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "execution/state/task_state_manager.py" not in blocked_paths


def test_a2a_task_api_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "api/a2a_tasks.py" not in blocked_paths


def test_common_server_utils_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "common/server/utils.py" not in blocked_paths


def test_common_package_has_no_module_or_app_shell_imports():
    violations = _common_import_violations()

    assert not violations, "Forbidden Common imports remain:\n" + "\n".join(violations)


def test_turn_id_helper_is_common_leaf_without_manifest_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="common_import_boundary")

    assert "common/utils/turn_id.py" not in blocked_paths


def test_a2a_helper_file_constants_are_common_leaf_without_import_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="common_import_boundary")

    assert "common/utils/a2a_helpers.py" not in blocked_paths


def test_context_utils_are_common_leaf_without_manifest_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="common_import_boundary")

    assert "common/utils/context_utils.py" not in blocked_paths


def test_orphaned_upload_cleaner_has_no_legacy_boundary_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="legacy_import_boundary")

    assert "jobs/cleanup_orphaned_uploads.py" not in blocked_paths


def test_compaction_sweep_has_no_legacy_boundary_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="legacy_import_boundary")

    assert "jobs/compaction_sweep.py" not in blocked_paths


def test_stale_task_checker_has_no_legacy_boundary_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="legacy_import_boundary")

    assert "jobs/stale_task_checker.py" not in blocked_paths


def test_retained_legacy_service_shims_do_not_keep_concrete_implementations():
    violations = _legacy_service_shim_violations()

    assert not violations, "Legacy service shims keep concrete implementations:\n" + "\n".join(
        violations
    )


def test_agent_rate_limit_service_shim_has_been_removed():
    assert not Path("services/rate_limit_service.py").exists()


def test_gateway_and_discovery_rate_limit_service_shims_have_been_removed():
    assert not Path("services/gateway_rate_limit_service.py").exists()
    assert not Path("services/discovery_rate_limit_service.py").exists()


def test_gateway_service_shim_has_been_removed():
    assert not Path("services/gateway_service.py").exists()


def test_file_upload_service_shim_has_been_removed():
    assert not Path("services/file_upload_service.py").exists()


def test_content_storage_service_shim_has_been_removed():
    assert not Path("services/content_storage_service.py").exists()


def test_old_implementation_packages_are_not_shipped_without_blocker():
    manifest = _manifest()
    blockers = manifest.get("blocked_cleanup", [])
    blocked_packages = {
        entry["path"]
        for entry in blockers
        if isinstance(entry.get("path"), str) and "/" not in entry["path"]
    }
    packages = set(tomllib.loads(Path("pyproject.toml").read_text())["tool"]["setuptools"]["packages"])
    shipped_legacy = sorted(packages & LEGACY_PACKAGES)
    unblocked_legacy = [
        package for package in shipped_legacy if package not in blocked_packages
    ]

    assert not unblocked_legacy, (
        "Legacy packages are still shipped without package-level cleanup blockers: "
        + ", ".join(unblocked_legacy)
    )


def test_legacy_workflow_cleanup_readiness_is_explicit():
    readiness = _manifest().get("legacy_workflow_decommission", {})
    ready = readiness.get("ready")
    evidence = readiness.get("evidence") or []

    assert isinstance(ready, bool), "Legacy workflow readiness must be explicit"
    if ready:
        assert evidence, "Legacy workflow cleanup is marked ready without evidence"
        return

    blockers = [
        item
        for item in evidence
        if item.get("classification") == "blocked_decommission_readiness"
    ]
    assert blockers, "Blocked legacy workflow cleanup needs explicit blocker evidence"
    for blocker in blockers:
        assert blocker.get("owner")
        assert blocker.get("reason")
        assert blocker.get("required_before_delete")
