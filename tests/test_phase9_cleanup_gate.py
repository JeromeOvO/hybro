import ast
import importlib
import json
import tomllib
from pathlib import Path

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
    "app_shell",
    "jobs",
    "models",
)

FORBIDDEN_PRODUCTION_IMPORT_PREFIXES = (
    "services",
    "modules",
    "database.mongodb",
    "config",
)

LEGACY_PACKAGES = {"config", "infrastructure", "modules", "services"}
REMOVED_LEGACY_OPERATIONAL_SCRIPTS = {
    "database/migration/add_agent_requests_indexes.py",
    "database/migration/add_cancelled_messages_indexes.py",
    "database/migration/add_capability_issue_indexes.py",
    "database/migration/add_discovery_api_requests_indexes.py",
    "database/migration/add_gateway_api_requests_indexes.py",
    "database/migration/add_hub_indexes.py",
    "database/migration/add_provider_legacy_flag.py",
    "database/migration/add_run_lifecycle_indexes.py",
    "database/migration/add_user_message_id_unique_index.py",
    "database/migration/backfill_agent_call_counts.py",
    "database/migration/backfill_agent_message_client_request_id.py",
    "database/migration/backfill_hub_agent_source.py",
    "database/migration/backfill_user_message_client_request_id.py",
    "database/migration/deduplicate_agents.py",
    "database/migration/fix_legacy_stale_tasks.py",
    "database/migration/migrate_room_memories.py",
    "database/migration/migrate_room_memory_to_conversation_history.py",
    "database/migration/null_legacy_room_processing_message_id.py",
    "database/migration/purge_empty_artifact_parts.py",
    "database/migration/rename_supervisor_v2_fields.py",
    "scripts/backfill_agent_status.py",
    "scripts/backfill_room_agent_message_parts.py",
    "scripts/backfill_timestamps_to_utc.py",
    "scripts/generate_api_key.py",
}
LEGACY_RUNTIME_ROOTS = tuple(sorted(LEGACY_PACKAGES))
PACKAGE_REMOVAL_RUNTIME_ROOTS = (
    "main.py",
    "container.py",
    "scripts",
    "database",
    *PRODUCTION_ROOTS,
    *LEGACY_RUNTIME_ROOTS,
)

FORBIDDEN_LEGACY_SHIM_IMPORT_PREFIXES = (
    "a2a",
    "database.mongodb",
    "config",
    "services",
)

FORBIDDEN_LEGACY_SHIM_GLOBALS = {"mongodb", "settings", "s3_service"}
FORBIDDEN_LEGACY_SHIM_CLASS_PREFIXES = ("_Legacy", "_Mongo")

FORBIDDEN_COMMON_IMPORT_PREFIXES = (
    "api",
    "api_gateway",
    "agent",
    "app_shell",
    "context_memory",
    "dal",
    "database",
    "services",
    "modules",
    "config",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "llm_gateway",
    "models",
    "platform_module",
    "a2a_adapter",
    "room",
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
    "app_shell",
)

PHASE9_IMPORT_SMOKE_MODULES = (
    "hub_runtime_bridge",
    "hub_runtime_bridge.service.hub_publish",
    "platform_module",
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


def _blocked_cleanup_prefixes(*, contract: str) -> set[tuple[str, str]]:
    prefixes: set[tuple[str, str]] = set()
    for entry in _manifest().get("blocked_cleanup", []):
        if entry.get("contract") != contract:
            continue
        path = entry.get("path")
        prefix = entry.get("forbidden_prefix")
        if isinstance(path, str) and isinstance(prefix, str):
            prefixes.add((path, prefix))
    return prefixes


def _is_blocked(path: Path, blocked_paths: set[str]) -> bool:
    rel = path.as_posix()
    return any(
        rel == blocked or rel.startswith(f"{blocked.rstrip('/')}/")
        for blocked in blocked_paths
    )


def _is_forbidden(module: str) -> bool:
    return _forbidden_prefix(module, FORBIDDEN_PRODUCTION_IMPORT_PREFIXES) is not None


def _forbidden_prefix(module: str, prefixes: tuple[str, ...]) -> str | None:
    for prefix in prefixes:
        if module == prefix or module.startswith(f"{prefix}."):
            return prefix
    return None


def _is_forbidden_import_blocked(
    path: Path,
    module: str,
    blocked_prefixes: set[tuple[str, str]],
) -> bool:
    rel = path.as_posix()
    return any(
        rel == blocked_path
        and (module == forbidden_prefix or module.startswith(f"{forbidden_prefix}."))
        for blocked_path, forbidden_prefix in blocked_prefixes
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
    blocked_prefixes = _blocked_cleanup_prefixes(contract="legacy_import_boundary")
    for path in _production_python_files():
        if path == Path("common/config/settings.py"):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        type_checking_lines: set[int] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"
            ):
                for child in ast.walk(node):
                    if hasattr(child, "lineno"):
                        type_checking_lines.add(child.lineno)
        for node in ast.walk(tree):
            if hasattr(node, "lineno") and node.lineno in type_checking_lines:
                continue
            if isinstance(node, ast.Import):
                names = [(alias.name, alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [
                    (f"{node.module}.{alias.name}", node.module) for alias in node.names
                ]
            else:
                continue
            for imported_name, module in names:
                if _is_forbidden(module) and not _is_forbidden_import_blocked(
                    path,
                    module,
                    blocked_prefixes,
                ):
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
                    names = [
                        (f"{node.module}.{alias.name}", node.module)
                        for alias in node.names
                    ]
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


def _file_sdk_import_violations(path: Path) -> list[str]:
    violations: list[str] = []
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
                names = [
                    (f"{node.module}.{alias.name}", node.module) for alias in node.names
                ]
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
                names = [
                    (f"{node.module}.{alias.name}", node.module) for alias in node.names
                ]
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


def _imports_package(path: Path, package: str) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    type_checking_lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    type_checking_lines.add(child.lineno)
    for node in ast.walk(tree):
        if hasattr(node, "lineno") and node.lineno in type_checking_lines:
            continue
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules = [node.module]
        else:
            continue
        if any(
            module == package or module.startswith(f"{package}.") for module in modules
        ):
            return True
    return False


def _import_modules_including_type_checking(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_names.append(node.module)
    return sorted(imported_names)


def _import_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    imported_names: list[str] = []
    type_checking_lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    type_checking_lines.add(child.lineno)
    for node in ast.walk(tree):
        if hasattr(node, "lineno") and node.lineno in type_checking_lines:
            continue
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_names.append(node.module)
    return sorted(imported_names)


EXPECTED_MAIN_LEGACY_IMPORTS = {}


REMOVED_OPERATIONAL_SCRIPTS = {
    "scripts/backfill_domain_aliases.py",
    "scripts/reupsert_agents_pinecone.py",
}


def _package_python_file_count(package: str) -> int:
    package_path = Path(package)
    if not package_path.exists():
        return 0
    return len(list(package_path.rglob("*.py")))


def _runtime_import_files_for_package(package: str) -> list[str]:
    files: list[Path] = []
    for root in PACKAGE_REMOVAL_RUNTIME_ROOTS:
        root_path = Path(root)
        if not root_path.exists():
            continue
        paths = [root_path] if root_path.is_file() else sorted(root_path.rglob("*.py"))
        for path in paths:
            if path.parts and path.parts[0] == package:
                continue
            if _imports_package(path, package):
                files.append(path)
    return [path.as_posix() for path in sorted(files)]


def _test_import_files_for_package(package: str) -> list[str]:
    return [
        path.as_posix()
        for path in sorted(Path("tests").rglob("*.py"))
        if _imports_package(path, package)
    ]


def test_repo_local_config_callers_use_common_config_settings():
    expected_callers = {
        Path("container.py"),
        Path("main.py"),
        Path("scripts/_discovery_client.py"),
    }
    violations: list[str] = []
    for path in sorted(expected_callers):
        modules = _import_modules_including_type_checking(path)
        if "config" in modules:
            violations.append(f"{path}: still imports deleted config package")
        if "common.config.settings" not in modules:
            violations.append(f"{path}: missing common.config.settings import")

    assert not violations, "Repo-local config callers are not migrated:\n" + "\n".join(
        violations
    )


def test_main_delegates_legacy_startup_import_inventory_to_container():
    main_modules = _import_modules(Path("main.py"))
    container_modules = _import_modules(Path("container.py"))

    main_actual = {
        package: sorted(
            {
                module
                for module in main_modules
                if module == package or module.startswith(f"{package}.")
            }
        )
        for package in EXPECTED_MAIN_LEGACY_IMPORTS
    }
    container_actual = {
        package: sorted(
            {
                module
                for module in container_modules
                if module == package or module.startswith(f"{package}.")
            }
        )
        for package in EXPECTED_MAIN_LEGACY_IMPORTS
    }

    assert main_actual == {package: [] for package in EXPECTED_MAIN_LEGACY_IMPORTS}
    assert container_actual == EXPECTED_MAIN_LEGACY_IMPORTS


def test_response_handler_has_no_services_imports_including_type_checking():
    path = Path("execution/dispatch/response_handler.py")
    modules = _import_modules_including_type_checking(path)
    violations = [
        module
        for module in modules
        if module == "services" or module.startswith("services" + ".")
    ]

    assert not violations, (
        "response_handler.py still imports legacy services, including type-only imports:\n"
        + "\n".join(violations)
    )


def test_operational_scripts_have_been_removed():
    manifest = _manifest()
    remaining_paths = sorted(
        path for path in REMOVED_OPERATIONAL_SCRIPTS if Path(path).exists()
    )

    assert "operational_script_" + "blockers" not in manifest
    assert not remaining_paths, "Operational scripts still exist:\n" + "\n".join(
        remaining_paths
    )


def _imported_names_including_type_checking(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported_names


def test_app_shell_runtime_blockers_match_main_inventory():
    manifest = _manifest()
    blockers = manifest.get("app_shell_runtime_blockers", [])
    blocker_by_package = {
        entry.get("package"): entry
        for entry in blockers
        if isinstance(entry.get("package"), str)
    }
    violations: list[str] = []

    for package in LEGACY_PACKAGES:
        if package in blocker_by_package:
            violations.append(
                f"{package}: app-shell runtime blocker remains after package deletion"
            )

    assert not violations, "App-shell runtime blockers are incomplete:\n" + "\n".join(
        violations
    )


def test_legacy_workflow_cleanup_manifest_has_been_removed():
    assert "legacy_workflow_" + "decommission" not in _manifest()


def test_no_production_imports_from_legacy_singletons():
    violations = _import_violations()

    assert not violations, "Legacy production imports remain:\n" + "\n".join(violations)


def test_legacy_import_boundary_blockers_are_prefix_exact():
    blocked = {("app_shell/example.py", "common.config.settings")}
    path = Path("app_shell/example.py")

    assert _is_forbidden_import_blocked(
        path,
        "common.config.settings",
        blocked,
    )
    assert _is_forbidden_import_blocked(
        path,
        "common.config.settings.runtime",
        blocked,
    )
    assert not _is_forbidden_import_blocked(path, "database.mongodb", blocked)
    assert not _is_forbidden_import_blocked(path, "a2a.client", blocked)
    assert not _is_forbidden_import_blocked(
        Path("app_shell/other.py"),
        "common.config.settings",
        blocked,
    )


def test_legacy_import_boundary_blockers_are_exact_current_files():
    bad: list[str] = []
    for entry in _manifest().get("blocked_cleanup", []):
        if entry.get("contract") != "legacy_import_boundary":
            continue
        path_value = entry.get("path")
        prefix = entry.get("forbidden_prefix")
        if not isinstance(path_value, str):
            bad.append(f"{entry}: missing path")
            continue
        if not isinstance(prefix, str):
            bad.append(f"{path_value}: missing forbidden_prefix")
            continue
        path = Path(path_value)
        if not path.exists() or path.is_dir():
            continue
        modules = _import_modules_including_type_checking(path)
        if not any(
            module == prefix or module.startswith(f"{prefix}.") for module in modules
        ):
            bad.append(f"{path}: missing live import for {prefix}")

    assert not bad, "Legacy import blockers are stale:\n" + "\n".join(bad)


def test_a2a_sdk_imports_are_confined_or_manifest_blocked():
    violations = _sdk_import_violations()

    assert not violations, "Undocumented A2A SDK imports remain:\n" + "\n".join(
        violations
    )


def test_phase9_sdk_confinement_gate_is_a2a_only_by_design():
    """AWS SDK confinement is enforced by test_dal_database_convergence_gate."""

    assert FORBIDDEN_SDK_IMPORT_PREFIXES == ("a2a",)


def test_phase9_import_smoke_modules_are_importable():
    missing: list[str] = []
    for module_name in PHASE9_IMPORT_SMOKE_MODULES:
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            missing.append(f"{module_name}: {exc}")

    assert not missing, "Phase 9 import smoke modules are missing:\n" + "\n".join(
        missing
    )


def test_no_numbered_duplicate_python_artifacts_are_shipped():
    duplicates = [
        path.as_posix()
        for path in sorted(Path(".").rglob("* 2.py"))
        if ".venv" not in path.parts
    ]

    assert not duplicates, "Numbered duplicate Python artifacts remain:\n" + "\n".join(
        duplicates
    )


def test_container_does_not_define_platform_adapter_classes_inline():
    tree = ast.parse(Path("container.py").read_text(), filename="container.py")
    inline_adapters = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name in {"RateLimitCollectionAdapter", "MongoFileMetadataRepository"}
    }

    assert inline_adapters == set()

    from platform_module.adapters import (
        MongoFileMetadataRepository,
        RateLimitCollectionAdapter,
    )

    assert RateLimitCollectionAdapter.__module__.startswith("platform_module.adapters.")
    assert MongoFileMetadataRepository.__module__.startswith(
        "platform_module.adapters."
    )


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


def test_common_types_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "common/types.py" not in blocked_paths


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


def test_execution_room_message_center_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "execution/orchestration/room_message_center.py" not in blocked_paths


def test_a2a_task_api_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "api/a2a_tasks.py" not in blocked_paths


def test_room_runtime_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "room/compat/runtime.py" not in blocked_paths


def test_remote_task_reader_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "a2a_adapter/remote_task_reader.py" not in blocked_paths
    assert not _file_sdk_import_violations(Path("a2a_adapter/remote_task_reader.py"))


def test_common_server_utils_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "common/server/utils.py" not in blocked_paths


def test_common_task_manager_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "common/server/task_manager.py" not in blocked_paths


def test_a2a_runtime_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "app_shell/a2a_runtime.py" not in blocked_paths


def test_common_server_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "common/server/server.py" not in blocked_paths


def test_common_remote_agent_connection_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "common/utils/remote_agent_connection.py" not in blocked_paths


def test_common_package_has_no_module_or_app_shell_imports():
    violations = _common_import_violations()

    assert not violations, "Forbidden Common imports remain:\n" + "\n".join(violations)


def test_phase9_cleanup_manifest_has_no_blocked_cleanup_entries():
    assert _manifest().get("blocked_cleanup", []) == []


def _manifest_string_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _manifest_string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _manifest_string_values(item)


def _package_removal_blocker_violations(manifest: dict) -> list[str]:
    violations: list[str] = []
    for index, entry in enumerate(manifest.get("package_removal_checklist") or []):
        if not isinstance(entry, dict):
            continue
        package = entry.get("package") or f"entry {index}"
        for key, value in sorted(entry.items()):
            if key.endswith("_blockers") and value:
                violations.append(
                    f"package_removal_checklist[{package!r}].{key}: {value}"
                )
    return violations


def test_phase9_cleanup_manifest_has_no_transitional_exceptions():
    manifest = _manifest()
    forbidden_tokens = ("temporary", "transitional", "deferred")
    violations = [
        f"{token}: {value}"
        for value in _manifest_string_values(manifest)
        for token in forbidden_tokens
        if token in value.lower()
    ]

    assert manifest.get("blocked_cleanup", []) == []
    assert manifest.get("app_shell_runtime_blockers", []) == []
    nested_blockers = _package_removal_blocker_violations(manifest)
    assert not nested_blockers, (
        "Phase 9 cleanup manifest still records package-removal blockers:\n"
        + "\n".join(nested_blockers)
    )
    assert not violations, (
        "Phase 9 cleanup manifest still records transitional exceptions:\n"
        + "\n".join(violations)
    )


def test_turn_id_helper_is_common_leaf_without_manifest_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="common_import_boundary")

    assert "common/utils/turn_id.py" not in blocked_paths


def test_a2a_helper_file_constants_are_common_leaf_without_import_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="common_import_boundary")

    assert "common/utils/a2a_helpers.py" not in blocked_paths


def test_a2a_helpers_have_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

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


def test_stale_task_checker_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "jobs/stale_task_checker.py" not in blocked_paths


def test_execution_webhook_transport_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "execution/dispatch/transports/webhook.py" not in blocked_paths


def test_execution_direct_transport_has_no_sdk_confinement_blocker():
    blocked_paths = _blocked_cleanup_paths(contract="sdk_confinement")

    assert "execution/dispatch/transports/direct.py" not in blocked_paths


def test_retained_legacy_service_shims_do_not_keep_concrete_implementations():
    violations = _legacy_service_shim_violations()

    assert not violations, (
        "Legacy service shims keep concrete implementations:\n" + "\n".join(violations)
    )


def test_agent_rate_limit_service_shim_has_been_removed():
    assert not Path("services/rate_limit_service.py").exists()


def test_gateway_and_discovery_rate_limit_service_shims_have_been_removed():
    assert not Path("services/gateway_rate_limit_service.py").exists()
    assert not Path("services/discovery_rate_limit_service.py").exists()


def test_gateway_service_shim_has_been_removed():
    assert not Path("services/gateway_service.py").exists()


def test_discovery_service_shim_has_been_removed():
    assert not Path("services/discovery_service.py").exists()


def test_file_upload_service_shim_has_been_removed():
    assert not Path("services/file_upload_service.py").exists()


def test_content_storage_service_shim_has_been_removed():
    assert not Path("services/content_storage_service.py").exists()


def test_removed_legacy_packages_are_recorded_in_cleanup_manifest():
    manifest = _manifest()
    checklist = manifest.get("package_removal_checklist") or []
    checklist_by_package = {
        entry.get("package"): entry
        for entry in checklist
        if isinstance(entry.get("package"), str)
    }
    safe_to_delete = set(manifest.get("safe_to_delete") or [])
    violations: list[str] = []

    for package in sorted(LEGACY_PACKAGES):
        entry = checklist_by_package.get(package)
        if entry is None:
            violations.append(f"{package}: missing package_removal_checklist entry")
            continue
        if entry.get("status") != "removed":
            violations.append(f"{package}: status must be removed")
        if entry.get("py_files") != _package_python_file_count(package):
            violations.append(f"{package}: py_files does not match current package")
        if entry.get("runtime_import_files") != _runtime_import_files_for_package(
            package
        ):
            violations.append(
                f"{package}: runtime_import_files does not match current imports"
            )
        if entry.get("test_import_files") != _test_import_files_for_package(package):
            violations.append(
                f"{package}: test_import_files does not match current imports"
            )
        if package not in safe_to_delete:
            violations.append(f"{package}: missing safe_to_delete entry")

    assert not violations, (
        "Removed legacy packages lack cleanup evidence:\n" + "\n".join(violations)
    )


def test_task_runtime_no_longer_exposes_legacy_task_center_crud():
    source = "\n".join(
        path.read_text()
        for path in (
            Path("a2a_adapter/remote_task_reader.py"),
            Path("execution/dispatch/transports/direct.py"),
        )
    )
    forbidden_tokens = {
        "TaskCenterRequest",
        "TaskCenterResponse",
        "BaseTask",
        "MetaTask",
        "TaskSession",
        "create_new_session",
        "create_new_base_task",
        "create_new_meta_task",
        "query_base_task_by_task_id",
        "query_meta_task_by_task_id",
        "query_all_sessions",
        "delete_meta_task_by_task_id",
        "update_meta_task_by_task_id",
        "query_base_tasks_by_session_id",
    }
    present = sorted(token for token in forbidden_tokens if token in source)

    assert not present, (
        "Task runtime still exposes legacy TaskCenter CRUD: " + ", ".join(present)
    )


def test_old_implementation_packages_are_not_shipped_without_blocker():
    manifest = _manifest()
    blockers = manifest.get("blocked_cleanup", [])
    blocked_packages = {
        entry["path"]
        for entry in blockers
        if isinstance(entry.get("path"), str) and "/" not in entry["path"]
    }
    packages = set(
        tomllib.loads(Path("pyproject.toml").read_text())["tool"]["setuptools"][
            "packages"
        ]
    )
    shipped_legacy = sorted(packages & LEGACY_PACKAGES)
    unblocked_legacy = [
        package for package in shipped_legacy if package not in blocked_packages
    ]

    assert not unblocked_legacy, (
        "Legacy packages are still shipped without package-level cleanup blockers: "
        + ", ".join(unblocked_legacy)
    )


def test_shipped_legacy_packages_have_package_removal_checklist_entries():
    manifest = _manifest()
    packages = set(
        tomllib.loads(Path("pyproject.toml").read_text())["tool"]["setuptools"][
            "packages"
        ]
    )
    shipped_legacy = sorted(packages & LEGACY_PACKAGES)
    checklist = manifest.get("package_removal_checklist") or []
    checklist_by_package = {
        entry.get("package"): entry
        for entry in checklist
        if isinstance(entry.get("package"), str)
    }
    violations: list[str] = []

    for package in shipped_legacy:
        entry = checklist_by_package.get(package)
        if entry is None:
            violations.append(f"{package}: missing package_removal_checklist entry")
            continue
        if entry.get("status") != "blocked":
            violations.append(f"{package}: status must remain blocked while shipped")
        if entry.get("py_files") != _package_python_file_count(package):
            violations.append(f"{package}: py_files does not match current package")
        if entry.get("runtime_import_files") != _runtime_import_files_for_package(
            package
        ):
            violations.append(
                f"{package}: runtime_import_files does not match current imports"
            )
        if entry.get("test_import_files") != _test_import_files_for_package(package):
            violations.append(
                f"{package}: test_import_files does not match current imports"
            )
        if not entry.get("required_before_remove"):
            violations.append(f"{package}: missing required_before_remove")
        if not (entry.get("runtime_blockers") or entry.get("test_blockers")):
            violations.append(f"{package}: missing runtime_blockers or test_blockers")

    assert not violations, (
        "Shipped legacy packages lack removal evidence:\n" + "\n".join(violations)
    )


def test_legacy_package_blocker_counts_match_package_removal_checklist():
    manifest = _manifest()
    checklist = {
        entry["package"]: entry
        for entry in manifest.get("package_removal_checklist", [])
        if isinstance(entry.get("package"), str)
    }
    blockers = {
        entry["path"]: entry
        for entry in manifest.get("blocked_cleanup", [])
        if isinstance(entry.get("path"), str) and entry["path"] in LEGACY_PACKAGES
    }
    violations: list[str] = []

    for package, entry in sorted(checklist.items()):
        blocker = blockers.get(package)
        if blocker is None:
            continue
        blockers_text = "\n".join(blocker.get("deletion_blockers") or [])
        runtime_count = len(entry.get("runtime_import_files") or [])
        test_count = len(entry.get("test_import_files") or [])
        if f"{runtime_count} runtime files" not in blockers_text:
            violations.append(
                f"{package}: missing {runtime_count} runtime files blocker"
            )
        if f"{test_count} test files" not in blockers_text:
            violations.append(f"{package}: missing {test_count} test files blocker")

    assert not violations, "Legacy package blocker counts are stale:\n" + "\n".join(
        violations
    )


def test_package_removal_runtime_scan_includes_shipped_legacy_roots():
    roots = set(PACKAGE_REMOVAL_RUNTIME_ROOTS)
    packages = set(
        tomllib.loads(Path("pyproject.toml").read_text())["tool"]["setuptools"][
            "packages"
        ]
    )
    shipped_legacy = packages & LEGACY_PACKAGES

    assert shipped_legacy.issubset(roots)


def test_legacy_database_dependent_operational_scripts_are_removed():
    present = [
        path
        for path in sorted(REMOVED_LEGACY_OPERATIONAL_SCRIPTS)
        if Path(path).exists()
    ]

    assert not present


def test_remaining_operational_scripts_do_not_import_deleted_database_runtime():
    forbidden = {
        "app_shell.database_service",
        "database.mongodb",
        "database.pinecone_db",
        "database.repository",
    }
    roots = (Path("database/migration"), Path("scripts"))
    violations: list[str] = []

    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            imported = set(_import_modules_including_type_checking(path))
            for module in sorted(forbidden & imported):
                violations.append(f"{path}:{module}")

    assert not violations


def test_query_similar_agents_with_scores_has_no_runtime_callers():
    token = "query_similar_agents_with_scores"
    violations = [
        path.as_posix()
        for path in _production_python_files()
        if token in path.read_text()
    ]

    assert not violations


def test_dal_database_convergence_manifest_exists_and_has_no_unknown_sections():
    manifest = json.loads(
        Path("tests/fixtures/dal_database_convergence_manifest.json").read_text()
    )
    assert set(manifest) == {
        "exclude_dirs",
        "exclude_files",
        "database_service_blockers",
        "mongo_singleton_blockers",
        "database_repository_blockers",
        "pinecone_singleton_blockers",
        "direct_pinecone_blockers",
        "legacy_runtime_files",
    }
    assert manifest["exclude_files"] == []
    assert manifest["legacy_runtime_files"] == []
    assert manifest["database_service_blockers"] == []
    assert manifest["mongo_singleton_blockers"] == []
    assert manifest["database_repository_blockers"] == []
    assert manifest["pinecone_singleton_blockers"] == []
    assert manifest["direct_pinecone_blockers"] == []


def test_sdk_shaped_adapter_helpers_are_adapter_only():
    from a2a_adapter import message_factory, task_requests

    forbidden_symbols = set(message_factory.__all__) | set(task_requests.__all__)
    forbidden_modules = {
        "a2a_adapter.message_factory",
        "a2a_adapter.task_requests",
    }
    violations: list[str] = []

    for path in _production_python_files():
        if path.parts and path.parts[0] == "a2a_adapter":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules:
                        violations.append(f"{path}:{node.lineno}: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module in forbidden_modules:
                    for alias in node.names:
                        if alias.name in forbidden_symbols or alias.name == "*":
                            violations.append(
                                f"{path}:{node.lineno}: {node.module}.{alias.name}"
                            )
                elif node.module == "a2a_adapter":
                    for alias in node.names:
                        if alias.name in {"message_factory", "task_requests"}:
                            violations.append(
                                f"{path}:{node.lineno}: {node.module}.{alias.name}"
                            )

    assert not violations
