import ast
import json
from pathlib import Path

import pytest

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

FORBIDDEN_MAIN_WIRING_IMPORT_PREFIXES = (
    "a2a_adapter",
    "agent",
    "app_shell.a2a_runtime",
    "app_shell.agent_capability_issue_service",
    "app_shell.agent_liveness_service",
    "app_shell.agent_matcher",
    "app_shell.agent_resolver_service",
    "app_shell.agent_runtime",
    "app_shell.agent_selection_service",
    "app_shell.agent_service",
    "app_shell.bedrock_service",
    "app_shell.compaction_service",
    "app_shell.context_assembly_service",
    "app_shell.context_memory_runtime",
    "app_shell.debate_service",
    "app_shell.delivery_runtime",
    "app_shell.execution_runtime",
    "app_shell.gemini_service",
    "app_shell.health_check",
    "app_shell.hitl_service",
    "app_shell.inspection_runtime",
    "app_shell.memory_search_service",
    "app_shell.memory_service",
    "app_shell.notification_service",
    "app_shell.openai_service",
    "app_shell.redis_runtime",
    "app_shell.relay_service",
    "app_shell.relay_store",
    "app_shell.repository_store",
    "app_shell.room_coordinator_service",
    "app_shell.room_lock",
    "app_shell.room_membership_source",
    "app_shell.room_runtime",
    "app_shell.s3_service",
    "app_shell.task_service",
    "app_shell.viewset",
    "context_memory.config",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "llm_gateway",
    "platform_module.adapters",
    "platform_module.rate_limit",
)

REQUIRED_MAIN_RUNTIME_ENTRYPOINTS = (
    "create_application_runtime",
    "startup_runtime",
    "shutdown_runtime",
    "validate_runtime_bindings",
)

FORBIDDEN_MAIN_WIRING_SNIPPETS = (
    "execution_room_message_center.bind(",
    "create_room_message_center(",
    "create_execution_facade(",
    "create_platform_facade(",
    "create_delivery_facade(",
    "a2a_tasks.bind_a2a_task_dependencies(",
    "agent.bind_agent_dependencies(",
    "agent.bind_agent_liveness_checker(",
    "agent_group.bind_agent_group_dependencies(",
    "discovery_api_keys.bind_api_key_store(",
    "files.bind_file_dependencies(",
    "gateway.bind_gateway_dependencies(",
    "hitl.bind_execution_deps(",
    "hitl.bind_room_ownership_reader(",
    "hub.bind_hub_dependencies(",
    "inspection_center.bind_inspection_dependencies(",
    "memory_center.bind_memory_dependencies(",
    "relay.bind_relay_dependencies(",
    "room_center.bind_execution_deps(",
    "room_center.bind_room_dependencies(",
    "sse.bind_execution_deps(",
    "sse.bind_sse_dependencies(",
    "viewset.bind_viewset_dependencies(",
    "agent_viewset.bind_agent_viewset_dependencies(",
    "webhooks.bind_webhook_dependencies(",
    "discovery.bind_discovery_dependencies(",
    "bind_api_gateway_deps(",
    "init_relay_service(",
)

FINAL_APP_SHELL_SHIMS = {
    "app_shell/room_runtime.py": {
        "max_lines": 80,
        "required_exports": {
            "AppShellRoomCenter",
            "DispatchStrategy",
            "RoomServices",
            "_ResolvedAttachments",
            "_human_size",
            "build_turn_content",
            "resolve_strategy",
            "room_runtime",
            "room_services",
        },
        "owning_module": "room.compat.runtime",
    },
    "app_shell/a2a_runtime.py": {
        "max_lines": 60,
        "required_exports": {"A2ARuntimeConfig", "A2AService", "a2a_service"},
        "owning_module": "a2a_adapter.runtime_service",
    },
    "app_shell/relay_service.py": {
        "max_lines": 80,
        "required_exports": {
            "RelayHubLivenessReader",
            "RelayService",
            "init_relay_service",
            "relay_service",
        },
        "owning_module": "hub_runtime_bridge.compat.relay_service",
    },
    "app_shell/context_assembly_service.py": {
        "max_lines": 70,
        "required_exports": {
            "ContextAssemblyResult",
            "ContextAssemblyService",
            "ContextMetrics",
            "TruncationReason",
            "context_assembly_service",
        },
        "owning_module": "context_memory.compat.context_assembly",
    },
    "app_shell/repository_store.py": {
        "max_lines": 80,
        "required_exports": {"AppShellRepositoryStore"},
        "owning_module": "dal.runtime_store.app_shell_store",
    },
}

FINAL_APP_SHELL_REEXPORT_SHIMS = {
    "app_shell/runtime_store_contracts.py": {
        "max_lines": 40,
        "owning_module": "dal.runtime_store.contracts",
    },
    "app_shell/repository_parts/__init__.py": {
        "max_lines": 40,
        "owning_module": "dal.runtime_store.parts",
    },
    "app_shell/repository_parts/agent_room_store.py": {
        "max_lines": 30,
        "owning_module": "dal.runtime_store.parts.agent_room_store",
    },
    "app_shell/repository_parts/message_store.py": {
        "max_lines": 30,
        "owning_module": "dal.runtime_store.parts.message_store",
    },
    "app_shell/repository_parts/task_lifecycle_store.py": {
        "max_lines": 30,
        "owning_module": "dal.runtime_store.parts.task_lifecycle_store",
    },
    "app_shell/repository_parts/hitl_store.py": {
        "max_lines": 30,
        "owning_module": "dal.runtime_store.parts.hitl_store",
    },
    "app_shell/repository_parts/memory_store.py": {
        "max_lines": 30,
        "owning_module": "dal.runtime_store.parts.memory_store",
    },
    "app_shell/repository_parts/parsing.py": {
        "max_lines": 70,
        "owning_module": "dal.runtime_store.parts.parsing",
    },
    "app_shell/repository_parts/webhook_tokens.py": {
        "max_lines": 40,
        "owning_module": "dal.runtime_store.parts.webhook_tokens",
    },
}

FOCUS_MODULES = {
    path.removesuffix(".py").replace("/", ".") for path in FINAL_APP_SHELL_SHIMS
}
FOCUS_APP_SHELL_NAMES = {
    module.removeprefix("app_shell.") for module in FOCUS_MODULES
}

PRODUCTION_MODULE_ROOTS = (
    "a2a_adapter",
    "agent",
    "api_gateway",
    "common",
    "context_memory",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "jobs",
    "platform_module",
    "room",
)


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
    return len(_public_business_methods(path))


def _public_business_methods(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    methods: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                methods.append(node.name)
            continue
        if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name.startswith("_") or _is_property_like(item):
                continue
            methods.append(f"{node.name}.{item.name}")
    return methods


def _line_count(path: Path) -> int:
    return sum(1 for _ in path.open())


def _module_exports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    exports: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.Import):
            exports.update(
                alias.asname or alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            exports.update(
                alias.asname or alias.name for alias in node.names if alias.name != "*"
            )
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            exports.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    exports.add(target.id)
                    if target.id == "__all__":
                        exports.update(_string_literal_sequence(node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            exports.add(node.target.id)

    return exports


def _string_literal_sequence(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set()
    return {item.value for item in node.elts if isinstance(item, ast.Constant)}


def _module_path(module: str) -> Path:
    module_base = Path(*module.split("."))
    module_file = module_base.with_suffix(".py")
    package_file = module_base / "__init__.py"
    if module_file.exists():
        return module_file
    if package_file.exists():
        return package_file
    return module_file


def _concrete_definitions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        f"{path}:{node.lineno}: {node.name}"
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _imports_module(path: Path, expected_module: str) -> bool:
    for _lineno, imported_module in _import_modules(path):
        if (
            imported_module == expected_module
            or imported_module.startswith(f"{expected_module}.")
        ):
            return True
    return False


def _app_shell_focus_runtime_imports_for_node(
    path: Path,
    node: ast.AST,
) -> list[str]:
    if isinstance(node, ast.Import):
        return [
            f"{path}:{node.lineno}: {alias.name}"
            for alias in node.names
            if alias.name in FOCUS_MODULES
        ]
    if not isinstance(node, ast.ImportFrom) or node.module is None:
        return []

    violations: list[str] = []
    if node.module in FOCUS_MODULES:
        violations.append(f"{path}:{node.lineno}: {node.module}")
    if node.module == "app_shell":
        violations.extend(
            f"{path}:{node.lineno}: app_shell.{alias.name}"
            for alias in node.names
            if alias.name in FOCUS_APP_SHELL_NAMES
        )
    return violations


def _app_shell_focus_runtime_import_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in sorted(paths):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            violations.extend(_app_shell_focus_runtime_imports_for_node(path, node))
    return violations


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


def test_app_shell_focus_files_are_final_import_shims():
    violations: list[str] = []

    for target, contract in sorted(FINAL_APP_SHELL_SHIMS.items()):
        path = Path(target)
        if not path.exists():
            violations.append(f"{target}: missing final import shim")
            continue

        line_count = _line_count(path)
        max_lines = contract["max_lines"]
        if line_count > max_lines:
            violations.append(f"{target}: {line_count} lines exceeds {max_lines}")

        exports = _module_exports(path)
        missing_exports = sorted(contract["required_exports"] - exports)
        if missing_exports:
            violations.append(
                f"{target}: missing required exports: {', '.join(missing_exports)}"
            )

        concrete_definitions = _concrete_definitions(path)
        if concrete_definitions:
            violations.append(
                f"{target}: concrete definitions remain:\n"
                + "\n".join(concrete_definitions)
            )

        public_methods = _public_business_methods(path)
        if public_methods:
            violations.append(
                f"{target}: public business methods remain "
                f"({_public_business_method_count(path)}): "
                + ", ".join(public_methods)
            )

        owning_module = contract["owning_module"]
        if not _imports_module(path, owning_module):
            violations.append(f"{target}: does not import owning module {owning_module}")

    assert not violations, "App-shell focus files are not final import shims:\n" + (
        "\n".join(violations)
    )


def test_app_shell_focus_owning_modules_exist_and_are_not_app_shell_owned():
    violations: list[str] = []

    contracts = {
        **FINAL_APP_SHELL_SHIMS,
        **FINAL_APP_SHELL_REEXPORT_SHIMS,
    }
    for target, contract in sorted(contracts.items()):
        owning_module = contract["owning_module"]
        owning_path = _module_path(owning_module)
        if owning_module == "app_shell" or owning_module.startswith("app_shell."):
            violations.append(f"{target}: owner remains in app_shell: {owning_module}")
        if not owning_path.exists():
            violations.append(f"{target}: owner module does not exist: {owning_module}")
        elif owning_path.parts and owning_path.parts[0] == "app_shell":
            violations.append(f"{target}: owner path remains in app_shell: {owning_path}")

    assert not violations, "App-shell shim owners are not final:\n" + "\n".join(
        violations
    )


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


def test_domain_modules_do_not_depend_on_app_shell_focus_runtime_modules():
    paths: list[Path] = []
    for root in PRODUCTION_MODULE_ROOTS:
        root_path = Path(root)
        if root_path.exists():
            paths.extend(sorted(root_path.rglob("*.py")))

    violations = _app_shell_focus_runtime_import_violations(paths)

    assert not violations, (
        "Domain modules still import app_shell focus runtime modules:\n"
        + "\n".join(violations)
    )


def test_focus_owning_modules_do_not_import_app_shell_runtime():
    violations: list[str] = []
    contracts = {
        **FINAL_APP_SHELL_SHIMS,
        **FINAL_APP_SHELL_REEXPORT_SHIMS,
    }

    for target, contract in sorted(contracts.items()):
        owning_module = contract["owning_module"]
        owning_path = _module_path(owning_module)
        if not owning_path.exists():
            violations.append(f"{target}: owner module does not exist: {owning_module}")
            continue
        owner_violations = _app_shell_focus_runtime_import_violations([owning_path])
        violations.extend(
            f"{owning_module}: {violation}" for violation in owner_violations
        )

    assert not violations, (
        "Focus owning modules still import app_shell runtime shims:\n"
        + "\n".join(violations)
    )


def test_app_shell_repository_submodules_are_final_reexport_shims():
    violations: list[str] = []

    for target, contract in sorted(FINAL_APP_SHELL_REEXPORT_SHIMS.items()):
        path = Path(target)
        if not path.exists():
            violations.append(f"{target}: missing final re-export shim")
            continue

        line_count = _line_count(path)
        max_lines = contract["max_lines"]
        if line_count > max_lines:
            violations.append(f"{target}: {line_count} lines exceeds {max_lines}")

        concrete_definitions = _concrete_definitions(path)
        if concrete_definitions:
            violations.append(
                f"{target}: concrete definitions remain:\n"
                + "\n".join(concrete_definitions)
            )

        owning_module = contract["owning_module"]
        if not _imports_module(path, owning_module):
            violations.append(f"{target}: does not import owning module {owning_module}")

    assert not violations, (
        "App-shell repository submodules are not final re-export shims:\n"
        + "\n".join(violations)
    )


def test_main_delegates_concrete_startup_wiring_to_container_runtime():
    main_source = Path("main.py").read_text()
    main_imports = _import_modules(Path("main.py"))
    violations: list[str] = []

    for lineno, module in main_imports:
        for prefix in FORBIDDEN_MAIN_WIRING_IMPORT_PREFIXES:
            if module == prefix or module.startswith(f"{prefix}."):
                violations.append(f"main.py:{lineno}: {module}")
                break

    for snippet in FORBIDDEN_MAIN_WIRING_SNIPPETS:
        if snippet in main_source:
            violations.append(f"main.py contains {snippet}")

    for entrypoint in REQUIRED_MAIN_RUNTIME_ENTRYPOINTS:
        if entrypoint not in main_source:
            violations.append(f"main.py missing {entrypoint}")

    assert not violations, "main.py still owns concrete startup wiring:\n" + "\n".join(
        violations
    )


@pytest.mark.asyncio
async def test_main_lifespan_shuts_down_runtime_when_validation_fails(monkeypatch):
    import main

    app = object()
    runtime = object()
    calls: list[tuple] = []

    def fake_create_application_runtime(app_settings):
        calls.append(("create", app_settings))
        return runtime

    async def fake_startup_runtime(startup_app, startup_runtime):
        calls.append(("startup", startup_app, startup_runtime))

    def fake_validate_runtime_bindings(validate_app, validate_runtime):
        calls.append(("validate", validate_app, validate_runtime))
        raise RuntimeError("validation failed")

    async def fake_shutdown_runtime(shutdown_app, shutdown_runtime):
        calls.append(("shutdown", shutdown_app, shutdown_runtime))

    monkeypatch.setattr(
        main, "create_application_runtime", fake_create_application_runtime
    )
    monkeypatch.setattr(main, "startup_runtime", fake_startup_runtime)
    monkeypatch.setattr(main, "validate_runtime_bindings", fake_validate_runtime_bindings)
    monkeypatch.setattr(main, "shutdown_runtime", fake_shutdown_runtime)

    with pytest.raises(RuntimeError, match="validation failed"):
        async with main.lifespan(app):
            calls.append(("yielded",))

    assert calls == [
        ("create", main.settings),
        ("startup", app, runtime),
        ("validate", app, runtime),
        ("shutdown", app, runtime),
    ]
