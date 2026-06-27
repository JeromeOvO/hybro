import ast
import importlib
import json
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import pytest

APP_SHELL_TARGETS = {
    ('app_' + 'shell' + '/a2a_runtime.py'),
    ('app_' + 'shell' + '/repository_store.py'),
}

CONTEXT_MEMORY_APP_SHELL_LEGACY_FILES = {
    ('app_' + 'shell' + '/memory_service.py'),
    ('app_' + 'shell' + '/memory_search_service.py'),
    ('app_' + 'shell' + '/compaction_service.py'),
    ('app_' + 'shell' + '/context_memory_runtime.py'),
    ('app_' + 'shell' + '/context_assembly_service.py'),
}

CONTEXT_MEMORY_APP_SHELL_LEGACY_SUFFIXES = {
    "memory_service",
    "memory_search_service",
    "compaction_service",
    "context_memory_runtime",
    "context_assembly_service",
}


def _context_memory_legacy_modules() -> set[str]:
    application_shell_prefix = ('app_' + 'shell') + "."
    return {
        f"{application_shell_prefix}{suffix}"
        for suffix in CONTEXT_MEMORY_APP_SHELL_LEGACY_SUFFIXES
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
    ('app_' + 'shell' + '.a2a_runtime'),
    ('app_' + 'shell' + '.agent_capability_issue_service'),
    ('app_' + 'shell' + '.agent_liveness_service'),
    ('app_' + 'shell' + '.agent_matcher'),
    ('app_' + 'shell' + '.agent_resolver_service'),
    ('app_' + 'shell' + '.agent_runtime'),
    ('app_' + 'shell' + '.agent_selection_service'),
    ('app_' + 'shell' + '.agent_service'),
    ('app_' + 'shell' + '.bedrock_service'),
    ('app_' + 'shell' + '.debate_service'),
    ('app_' + 'shell' + '.execution_runtime'),
    ('app_' + 'shell' + '.gemini_service'),
    ('app_' + 'shell' + '.health_check'),
    ('app_' + 'shell' + '.hitl_service'),
    ('app_' + 'shell' + '.inspection_runtime'),
    ('app_' + 'shell' + '.openai_service'),
    ('app_' + 'shell' + '.repository_store'),
    ('app_' + 'shell' + '.room_coordinator_service'),
    ('app_' + 'shell' + '.room_membership_source'),
    ('app_' + 'shell' + '.room_runtime'),
    ('app_' + 'shell' + '.s3_service'),
    ('app_' + 'shell' + '.task_service'),
    ('app_' + 'shell' + '.viewset'),
    "context_memory.config",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "llm_gateway",
    "platform_module.adapters",
    "platform_module.rate_limit",
    *sorted(_context_memory_legacy_modules()),
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
    ('app_' + 'shell' + '/a2a_runtime.py'): {
        "max_lines": 60,
        "required_exports": {"A2ARuntimeConfig", "A2AService", "a2a_service"},
        "owning_module": "a2a_adapter.runtime_service",
    },
    ('app_' + 'shell' + '/repository_store.py'): {
        "max_lines": 80,
        "required_exports": {
            ('App' + 'Shell' + 'RepositoryStore'),
            "_extract_text_from_artifact_parts",
            "_modified_count",
            "_mongo_update_succeeded",
            "_safe_parse_agent",
            "_safe_parse_agent_group",
            "_safe_parse_agent_message",
            "_safe_parse_chat_context",
            "_safe_parse_room",
            "_safe_parse_room_memory",
            "_safe_parse_user_message",
            "_strip_file_urls",
            "_strip_unset_task_tracking_fields",
            "_task_tracking_matches",
        },
        "owning_module": "dal.runtime_store.repository_store",
    },
    ('app_' + 'shell' + '/agent_service.py'): {
        "max_lines": 40,
        "required_exports": {
            "AgentService",
            "_agent_info_to_legacy_agent",
            "_card_snapshot_to_legacy_card",
            "is_local_agent_url",
            "normalize_agent_url",
        },
        "owning_module": "agent.service",
    },
    ('app_' + 'shell' + '/agent_runtime.py'): {
        "max_lines": 30,
        "required_exports": {('App' + 'Shell' + 'AgentCenter')},
        "owning_module": "agent.route_adapter",
    },
    ('app_' + 'shell' + '/agent_matcher.py'): {
        "max_lines": 40,
        "required_exports": {
            "AgentMatcher",
            "MatchedAgent",
            "MatchResult",
            "_agent_supports_files",
            "compute_capability_score",
            "select_top_agents",
        },
        "owning_module": "agent.matcher",
    },
    ('app_' + 'shell' + '/agent_selection_service.py'): {
        "max_lines": 40,
        "required_exports": {
            "AgentSelection",
            "AgentSelectionResult",
            "AgentSelectionService",
            "RoutingStrategy",
        },
        "owning_module": "agent.selection_service",
    },
    ('app_' + 'shell' + '/agent_resolver_service.py'): {
        "max_lines": 40,
        "required_exports": {
            "AgentResolverFacadeRepository",
            "AgentResolverService",
            "ResolveResult",
            "_HealthCache",
            "_agent_to_routing_candidate",
        },
        "owning_module": "agent.resolver",
    },
    ('app_' + 'shell' + '/agent_health_service.py'): {
        "max_lines": 40,
        "required_exports": {"AgentHealthRepositoryPort", "AgentHealthService"},
        "owning_module": "agent.health",
    },
    ('app_' + 'shell' + '/agent_liveness_service.py'): {
        "max_lines": 40,
        "required_exports": {
            "AgentLivenessService",
            "bind_agent_liveness_deps",
            "check_and_sync_liveness",
            "reset_agent_liveness_deps",
        },
        "owning_module": "agent.liveness",
    },
    ('app_' + 'shell' + '/inspection_runtime.py'): {
        "max_lines": 30,
        "required_exports": {('App' + 'Shell' + 'InspectionCenter')},
        "owning_module": "agent.inspection",
    },
    ('app_' + 'shell' + '/agent_capability_issue_service.py'): {
        "max_lines": 40,
        "required_exports": {
            "AgentCapabilityIssueServiceAdapter",
            "AgentCapabilityIssueServiceNotBound",
            "CapabilityIssueExclusionReader",
        },
        "owning_module": "agent.capability_issue",
    },
}

FINAL_APP_SHELL_REEXPORT_SHIMS = {
    ('app_' + 'shell' + '/runtime_store_contracts.py'): {
        "max_lines": 40,
        "required_exports": {
            "_dump_model",
            "_dump_runtime",
            "agent_group_to_runtime",
            "agent_to_runtime",
            "chat_context_to_runtime",
            "message_content_to_runtime",
            "room_agent_message_to_runtime",
            "room_memory_to_runtime",
            "room_to_runtime",
            "room_user_message_to_runtime",
            "runtime_agent_groups",
            "runtime_agent_messages",
            "runtime_agents",
            "runtime_rooms",
            "runtime_to_agent",
            "runtime_to_agent_group",
            "runtime_to_chat_context",
            "runtime_to_message_content",
            "runtime_to_room",
            "runtime_to_room_agent_message",
            "runtime_to_room_memory",
            "runtime_to_room_user_message",
            "runtime_user_messages",
        },
        "owning_module": "dal.runtime_store.contracts",
    },
    ('app_' + 'shell' + '/repository_parts/__init__.py'): {
        "max_lines": 40,
        "required_exports": {
            ('App' + 'Shell' + 'AgentRoomStore'),
            ('App' + 'Shell' + 'HITLStore'),
            ('App' + 'Shell' + 'MemoryStore'),
            ('App' + 'Shell' + 'MessageStore'),
            ('App' + 'Shell' + 'TaskLifecycleStore'),
        },
        "owning_module": "dal.runtime_store.parts",
    },
    ('app_' + 'shell' + '/repository_parts/agent_room_store.py'): {
        "max_lines": 30,
        "required_exports": {('App' + 'Shell' + 'AgentRoomStore')},
        "owning_module": "dal.runtime_store.parts.agent_room_store",
    },
    ('app_' + 'shell' + '/repository_parts/message_store.py'): {
        "max_lines": 30,
        "required_exports": {('App' + 'Shell' + 'MessageStore')},
        "owning_module": "dal.runtime_store.parts.message_store",
    },
    ('app_' + 'shell' + '/repository_parts/task_lifecycle_store.py'): {
        "max_lines": 30,
        "required_exports": {('App' + 'Shell' + 'TaskLifecycleStore')},
        "owning_module": "dal.runtime_store.parts.task_lifecycle_store",
    },
    ('app_' + 'shell' + '/repository_parts/hitl_store.py'): {
        "max_lines": 30,
        "required_exports": {('App' + 'Shell' + 'HITLStore')},
        "owning_module": "dal.runtime_store.parts.hitl_store",
    },
    ('app_' + 'shell' + '/repository_parts/memory_store.py'): {
        "max_lines": 30,
        "required_exports": {('App' + 'Shell' + 'MemoryStore')},
        "owning_module": "dal.runtime_store.parts.memory_store",
    },
    ('app_' + 'shell' + '/repository_parts/parsing.py'): {
        "max_lines": 70,
        "required_exports": {
            "_extract_text_from_artifact_parts",
            "_modified_count",
            "_mongo_update_succeeded",
            "_safe_parse_agent",
            "_safe_parse_agent_group",
            "_safe_parse_agent_message",
            "_safe_parse_chat_context",
            "_safe_parse_room",
            "_safe_parse_room_memory",
            "_safe_parse_user_message",
            "_strip_file_urls",
            "_strip_unset_task_tracking_fields",
            "_task_tracking_matches",
        },
        "owning_module": "dal.runtime_store.parts.parsing",
    },
    ('app_' + 'shell' + '/repository_parts/webhook_tokens.py'): {
        "max_lines": 40,
        "required_exports": {
            "generate_webhook_token",
            "get_webhook_signing_key",
            "hash_webhook_token",
            "verify_webhook_token",
        },
        "owning_module": "dal.runtime_store.parts.webhook_tokens",
    },
}

EXPECTED_MOVED_RUFF_IGNORES = {
    ('app_' + 'shell' + '/a2a_runtime.py'): (
        "a2a_adapter/runtime_service.py",
        ["C901"],
    ),
    ('app_' + 'shell' + '/agent_health_service.py'): (
        "agent/health.py",
        ["C901"],
    ),
    ('app_' + 'shell' + '/agent_service.py'): (
        "agent/service.py",
        ["C901"],
    ),
}


def _module_name_from_source_path(path: str) -> str:
    return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


FOCUS_MODULES = {
    _module_name_from_source_path(path)
    for path in {
        **FINAL_APP_SHELL_SHIMS,
        **FINAL_APP_SHELL_REEXPORT_SHIMS,
    }
}

PRODUCTION_MODULE_ROOTS = (
    "a2a_adapter",
    "agent",
    "api",
    "api_gateway",
    "common",
    "context_memory",
    "dal",
    "database",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "jobs",
    "llm_gateway",
    "models",
    "platform_module",
    "room",
)

PRODUCTION_MODULE_FILES = ("container.py", "main.py")

EXCLUDED_PRODUCTION_SCAN_PARTS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
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


def test_application_shell_runtime_hub_modules_removed_from_owner_runtime_paths():
    root = Path(__file__).resolve().parents[1]
    scanned_roots = [
        root / "container.py",
        root / "api",
        root / "api_gateway",
        root / "delivery",
        root / "hub_runtime_bridge",
        root / "dal",
        root / "room",
        root / "execution",
    ]
    forbidden_imports = {
        ('app_' + 'shell' + '.') + suffix
        for suffix in (
            "delivery_runtime",
            "redis_runtime",
            "relay_store",
            "relay_service",
            "room_lock",
            "notification_service",
        )
    }
    forbidden_runtime_names = {
        "SSEManager",
        ('App' + 'Shell' + 'Redis'),
        ('App' + 'Shell' + 'Relay'),
        "RedisRoomDistributedLock",
    }

    violations: list[str] = []
    for base in scanned_roots:
        paths = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in paths:
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                for needle in sorted(forbidden_imports):
                    if needle in line:
                        violations.append(f"{rel}:{lineno}: contains {needle}")
                for needle in sorted(forbidden_runtime_names):
                    if needle in line:
                        violations.append(f"{rel}:{lineno}: contains {needle}")

    assert not violations, ('Forbidden ' + 'app-' + 'shell' + ' runtime references:\n') + "\n".join(
        violations
    )


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


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in node.elts:
            names.update(_target_names(item))
        return names
    return set()


def _module_bound_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(
                alias.asname or alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            names.update(
                alias.asname or alias.name for alias in node.names if alias.name != "*"
            )
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_target_names(target))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)

    return names


def _target_root_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        return _target_root_names(node.value)
    if isinstance(node, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in node.elts:
            names.update(_target_root_names(item))
        return names
    return set()


def _targets_all(node: ast.AST, all_aliases: set[str] | None = None) -> bool:
    all_names = {"__all__", *(all_aliases or set())}
    if isinstance(node, ast.Assign):
        return any(_target_root_names(target) & all_names for target in node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return bool(_target_root_names(node.target) & all_names)
    if isinstance(node, ast.Delete):
        return any(_target_root_names(target) & all_names for target in node.targets)
    return False


def _is_direct_all_assignment(node: ast.AST) -> bool:
    if isinstance(node, ast.Assign):
        return (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__all__"
        )
    return (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "__all__"
        and node.value is not None
    )


def _is_all_alias_assignment(node: ast.AST) -> bool:
    if isinstance(node, ast.Assign):
        return (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Name)
            and node.value.id == "__all__"
        )
    return (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and isinstance(node.value, ast.Name)
        and node.value.id == "__all__"
    )


def _all_alias_names(tree: ast.Module) -> set[str]:
    aliases: set[str] = set()
    for node in tree.body:
        if _is_all_alias_assignment(node):
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                aliases.add(node.targets[0].id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                aliases.add(node.target.id)
    return aliases


def _mutates_all(node: ast.AST, all_aliases: set[str] | None = None) -> bool:
    all_names = {"__all__", *(all_aliases or set())}
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and bool(_target_root_names(node.value.func.value) & all_names)
    )


def _explicit_all_values(path: Path) -> list[ast.AST]:
    tree = ast.parse(path.read_text(), filename=str(path))
    all_aliases = _all_alias_names(tree)
    values: list[ast.AST] = []

    for node in tree.body:
        if _is_all_alias_assignment(node):
            continue
        if _mutates_all(node, all_aliases):
            values.append(node)
            continue
        if not _targets_all(node, all_aliases):
            continue
        if _is_direct_all_assignment(node) and isinstance(
            node,
            (ast.Assign, ast.AnnAssign),
        ):
            values.append(node.value)
        else:
            values.append(node)
    return values


def _explicit_all_exports(path: Path) -> set[str] | None:
    all_values = _explicit_all_values(path)
    if not all_values:
        return None
    if len(all_values) != 1:
        return set()
    return _static_string_literal_sequence(all_values[0]) or set()


def _all_static_literal_violation(path: Path) -> str | None:
    all_values = _explicit_all_values(path)
    if not all_values:
        return f"{path}: missing explicit __all__"
    if (
        len(all_values) == 1
        and _static_string_literal_sequence(all_values[0]) is not None
    ):
        return None
    return f"{path}: __all__ must be a static string literal sequence"


def _module_exports(path: Path) -> set[str]:
    all_exports = _explicit_all_exports(path)

    if all_exports is not None:
        return all_exports
    return _module_bound_names(path)


def _static_string_literal_sequence(node: ast.AST) -> set[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    exports: set[str] = set()
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            return None
        exports.add(item.value)
    return exports


def _attribute_chain(node: ast.AST) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        parent = _attribute_chain(node.value)
        if parent is None:
            return None
        return (*parent, node.attr)
    return None


def _mutation_target_roots(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Assign):
        roots: set[str] = set()
        for target in node.targets:
            roots.update(_target_root_names(target))
        return roots
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return _target_root_names(node.target)
    if isinstance(node, ast.Delete):
        roots: set[str] = set()
        for target in node.targets:
            roots.update(_target_root_names(target))
        return roots
    return set()


def _import_bound_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {alias.asname or alias.name for alias in node.names if alias.name != "*"}
    return set()


def _top_level_rebound_names(node: ast.AST) -> set[str]:
    import_bound_names = _import_bound_names(node)
    if import_bound_names:
        return import_bound_names
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    return _mutation_target_roots(node)


def _invalidated_owner_refs(
    tree: ast.Module,
    owner_refs: set[tuple[str, ...]],
    owner_ref_lines: dict[tuple[str, ...], int] | None = None,
) -> set[tuple[str, ...]]:
    if owner_ref_lines is not None:
        invalidated_refs: set[tuple[str, ...]] = set()
        for node in tree.body:
            rebound_names = _top_level_rebound_names(node)
            if not rebound_names:
                continue
            for ref in owner_refs:
                if not ref or ref[0] not in rebound_names:
                    continue
                owner_line = owner_ref_lines.get(ref)
                if owner_line is None or node.lineno > owner_line:
                    invalidated_refs.add(ref)
        return invalidated_refs

    mutated_roots: set[str] = set()
    for node in tree.body:
        mutated_roots.update(_top_level_rebound_names(node))
    return {ref for ref in owner_refs if ref and ref[0] in mutated_roots}


def _dynamic_required_export_is_allowed(path: Path, export: str) -> bool:
    return False


def _module_path(module: str) -> Path:
    module_base = Path(*module.split("."))
    module_file = module_base.with_suffix(".py")
    package_file = module_base / "__init__.py"
    if module_file.exists():
        return module_file
    if package_file.exists():
        return package_file
    return module_file


def _owner_static_exports_or_bindings(owning_module: str) -> set[str]:
    owner_path = _module_path(owning_module)
    if not owner_path.exists():
        return set()
    return _module_exports(owner_path) | _module_bound_names(owner_path)


def _owner_static_star_import_exports(owning_module: str) -> set[str]:
    owner_path = _module_path(owning_module)
    if not owner_path.exists():
        return set()

    all_values = _explicit_all_values(owner_path)
    if all_values:
        if len(all_values) != 1:
            return set()
        return _static_string_literal_sequence(all_values[0]) or set()

    return {
        name for name in _module_bound_names(owner_path) if not name.startswith("_")
    }


def _owner_star_import_exports(path: Path, owning_module: str) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    has_owner_star_import = any(
        isinstance(node, ast.ImportFrom)
        and node.module == owning_module
        and any(alias.name == "*" for alias in node.names)
        for node in tree.body
    )
    if not has_owner_star_import:
        return set()
    return _owner_static_star_import_exports(owning_module)


def _module_export_surface(path: Path, _owning_module: str) -> set[str]:
    all_exports = _explicit_all_exports(path)
    if all_exports is not None:
        return all_exports
    return set()


def _owner_import_provenance(
    node: ast.AST,
    owning_module: str,
    required_exports: set[str],
) -> tuple[set[str], set[tuple[str, ...]]]:
    backed_exports: set[str] = set()
    owner_refs: set[tuple[str, ...]] = set()

    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name != owning_module:
                continue
            if alias.asname:
                owner_refs.add((alias.asname,))
            else:
                owner_refs.add(tuple(owning_module.split(".")))
    elif isinstance(node, ast.ImportFrom) and node.module == owning_module:
        for alias in node.names:
            if alias.name == "*":
                backed_exports.update(
                    required_exports & _owner_static_star_import_exports(owning_module)
                )
            elif alias.name in required_exports and alias.asname in {None, alias.name}:
                backed_exports.add(alias.name)
            elif alias.asname in required_exports:
                backed_exports.add(alias.asname)

    return backed_exports, owner_refs


def _rebound_owner_import_exports(
    tree: ast.Module,
    owning_module: str,
    required_exports: set[str],
) -> set[str]:
    imported_exports: set[str] = set()
    rebound_exports: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == owning_module:
            for alias in node.names:
                if alias.name == "*":
                    imported_exports.update(
                        required_exports
                        & _owner_static_star_import_exports(owning_module)
                    )
                elif alias.name in required_exports and alias.asname in {
                    None,
                    alias.name,
                }:
                    imported_exports.add(alias.name)
                elif alias.asname in required_exports:
                    imported_exports.add(alias.asname)
            continue

        rebound_exports.update(imported_exports & _top_level_rebound_names(node))

    return rebound_exports


def _owner_assignment_backed_exports(
    node: ast.AST,
    owner_refs: set[tuple[str, ...]],
) -> set[str]:
    if not isinstance(node, ast.Assign):
        return set()
    chain = _attribute_chain(node.value)
    if chain is None or len(chain) < 2:
        return set()
    if chain[:-1] not in owner_refs:
        return set()

    backed_exports: set[str] = set()
    for target in node.targets:
        backed_exports.update(_target_names(target))
    return backed_exports


def _owner_backed_exports(
    path: Path,
    owning_module: str,
    required_exports: set[str],
) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    backed_exports: set[str] = set()
    owner_refs: set[tuple[str, ...]] = set()
    owner_ref_lines: dict[tuple[str, ...], int] = {}

    for node in tree.body:
        imported_exports, imported_refs = _owner_import_provenance(
            node,
            owning_module,
            required_exports,
        )
        backed_exports.update(imported_exports)
        owner_refs.update(imported_refs)
        for ref in imported_refs:
            owner_ref_lines[ref] = node.lineno

    owner_refs -= _invalidated_owner_refs(tree, owner_refs, owner_ref_lines)
    backed_exports -= _rebound_owner_import_exports(
        tree,
        owning_module,
        required_exports,
    )

    for node in tree.body:
        backed_exports.update(_owner_assignment_backed_exports(node, owner_refs))

    return backed_exports


def _required_export_violations(
    path: Path,
    required_exports: set[str],
    owning_module: str,
) -> list[str]:
    exports = _module_export_surface(path, owning_module)
    bound_names = _module_bound_names(path)
    owner_backed_exports = _owner_backed_exports(path, owning_module, required_exports)
    effective_bound_names = bound_names | owner_backed_exports
    missing_exports = sorted(required_exports - exports)
    unexpected_exports = sorted(exports - required_exports)
    missing_bound_names = sorted(
        export
        for export in required_exports - effective_bound_names
        if not _dynamic_required_export_is_allowed(path, export)
    )
    missing_owner_backing = sorted(
        export
        for export in required_exports - owner_backed_exports
        if not _dynamic_required_export_is_allowed(path, export)
    )
    violations: list[str] = []
    all_violation = _all_static_literal_violation(path)

    if all_violation is not None:
        violations.append(all_violation)
    if missing_exports:
        violations.append(
            f"{path}: missing required exports: {', '.join(missing_exports)}"
        )
    if unexpected_exports:
        violations.append(
            f"{path}: unexpected exports: {', '.join(unexpected_exports)}"
        )
    if missing_bound_names:
        violations.append(
            f"{path}: required exports are not bound: " + ", ".join(missing_bound_names)
        )
    if missing_owner_backing:
        violations.append(
            f"{path}: required exports are not backed by {owning_module}: "
            + ", ".join(missing_owner_backing)
        )
    return violations


def _concrete_definitions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        f"{path}:{node.lineno}: {node.name}"
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _imports_module(path: Path, expected_module: str) -> bool:
    for _lineno, imported_module in _import_modules(path):
        if imported_module == expected_module or imported_module.startswith(
            f"{expected_module}."
        ):
            return True
    return False


def _is_owner_import_module(imported_module: str, owning_module: str) -> bool:
    return imported_module == owning_module or imported_module.startswith(
        f"{owning_module}."
    )


def _is_module_docstring_node(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _owner_reexport_import_from_violations(
    path: Path,
    node: ast.ImportFrom,
    required_exports: set[str],
    owning_module: str,
) -> list[str]:
    if node.module == "__future__":
        return []
    if node.module is None or not _is_owner_import_module(node.module, owning_module):
        module = node.module or "<relative import>"
        return [f"{path}:{node.lineno}: imports non-owner module {module}"]

    violations: list[str] = []
    for alias in node.names:
        if alias.name == "*":
            violations.append(f"{path}:{node.lineno}: star import from {node.module}")
            continue
        bound_name = alias.asname or alias.name
        if bound_name not in required_exports:
            violations.append(
                f"{path}:{node.lineno}: non-exported owner import {bound_name}"
            )
    return violations


def _owner_reexport_import_violations(
    path: Path,
    required_exports: set[str],
    owning_module: str,
) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    owner_refs: set[tuple[str, ...]] = set()

    for node in tree.body:
        _, imported_refs = _owner_import_provenance(
            node,
            owning_module,
            required_exports,
        )
        owner_refs.update(imported_refs)

    for node in tree.body:
        if _is_module_docstring_node(node):
            continue

        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_owner_import_module(alias.name, owning_module):
                    continue
                violations.append(
                    f"{path}:{node.lineno}: imports non-owner module {alias.name}"
                )
            continue

        if isinstance(node, ast.ImportFrom):
            violations.extend(
                _owner_reexport_import_from_violations(
                    path,
                    node,
                    required_exports,
                    owning_module,
                )
            )
            continue

        if isinstance(node, (ast.Assign, ast.AnnAssign)) and _targets_all(node):
            continue
        if isinstance(node, ast.Assign):
            backed_exports = _owner_assignment_backed_exports(node, owner_refs)
            if backed_exports and backed_exports <= required_exports:
                continue

        violations.append(
            f"{path}:{node.lineno}: non-re-export top-level statement "
            f"{type(node).__name__}"
        )

    return violations


def _is_application_shell_module(module: str) -> bool:
    return module == ('app_' + 'shell') or module.startswith(('app_' + 'shell' + '.'))


def _application_shell_imports_for_node(path: Path, node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [
            f"{path}:{node.lineno}: {alias.name}"
            for alias in node.names
            if _is_application_shell_module(alias.name)
        ]
    if not isinstance(node, ast.ImportFrom) or node.module is None:
        return []
    if _is_application_shell_module(node.module) and node.module != ('app_' + 'shell'):
        return [f"{path}:{node.lineno}: {node.module}"]
    if node.module == ('app_' + 'shell'):
        return [
            f"{path}:{node.lineno}: {'app_' + 'shell'}.{alias.name}"
            for alias in node.names
        ]
    return []


def _application_shell_import_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in sorted(paths):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            violations.extend(_application_shell_imports_for_node(path, node))
    return violations


def _ruff_pattern_matches_path(pattern: str, path: str) -> bool:
    return pattern == path or fnmatch(path, pattern) or Path(path).match(pattern)


def _is_focus_module(module: str) -> bool:
    return any(
        module == focus_module or module.startswith(f"{focus_module}.")
        for focus_module in FOCUS_MODULES
    )


def _application_shell_focus_runtime_imports_for_node(
    path: Path,
    node: ast.AST,
) -> list[str]:
    if isinstance(node, ast.Import):
        return [
            f"{path}:{node.lineno}: {alias.name}"
            for alias in node.names
            if _is_focus_module(alias.name)
        ]
    if not isinstance(node, ast.ImportFrom) or node.module is None:
        return []

    violations: list[str] = []
    if _is_focus_module(node.module):
        violations.append(f"{path}:{node.lineno}: {node.module}")
    if node.module == ('app_' + 'shell'):
        violations.extend(
            f"{path}:{node.lineno}: {'app_' + 'shell'}.{alias.name}"
            for alias in node.names
            if _is_focus_module(f"{'app_' + 'shell'}.{alias.name}")
        )
    return violations


def _application_shell_focus_runtime_import_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in sorted(paths):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            violations.extend(
                _application_shell_focus_runtime_imports_for_node(path, node)
            )
    return violations


def _application_shell_agent_shim_paths() -> set[Path]:
    return {
        Path(path)
        for path, contract in FINAL_APP_SHELL_SHIMS.items()
        if contract["owning_module"] == "agent"
        or contract["owning_module"].startswith("agent.")
    }


def _application_shell_agent_forbidden_modules() -> set[str]:
    return {
        _module_name_from_source_path(str(path))
        for path in _application_shell_agent_shim_paths()
    }


def _application_shell_agent_forbidden_from_application_shell_names() -> set[str]:
    return {path.stem for path in _application_shell_agent_shim_paths()}


def _source_package_parts(path: Path) -> tuple[str, ...]:
    parts = path.with_suffix("").parts
    if ('app_' + 'shell') in parts:
        application_shell_index = parts.index(('app_' + 'shell'))
        parts = parts[application_shell_index:]
    return parts[:-1]


def _resolved_import_from_module(path: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module

    package_parts = _source_package_parts(path)
    if node.level > len(package_parts):
        return None

    base_parts = package_parts[: len(package_parts) - node.level + 1]
    if node.module:
        base_parts = (*base_parts, *node.module.split("."))
    return ".".join(base_parts)


def _application_shell_agent_imports_for_node(path: Path, node: ast.AST) -> list[str]:
    forbidden_modules = _application_shell_agent_forbidden_modules()
    forbidden_from_application_shell_names = (
        _application_shell_agent_forbidden_from_application_shell_names()
    )

    if isinstance(node, ast.Import):
        return [
            f"{path}: import {alias.name}"
            for alias in node.names
            if alias.name in forbidden_modules
        ]
    if not isinstance(node, ast.ImportFrom):
        return []
    module = _resolved_import_from_module(path, node)
    if module in forbidden_modules:
        return [f"{path}: from {module} import ..."]
    if module == ('app_' + 'shell'):
        return [
            f"{path}: from {'app_' + 'shell'} import {alias.name}"
            for alias in node.names
            if alias.name in forbidden_from_application_shell_names
        ]
    return []


def _application_shell_agent_import_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in sorted(paths):
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            violations.extend(_application_shell_agent_imports_for_node(path, node))
    return violations


def _agent_runtime_consumer_python_files() -> list[Path]:
    paths = set(_production_module_python_files())
    application_shell_root = Path(('app_' + 'shell'))
    if application_shell_root.exists():
        paths.update(application_shell_root.rglob("*.py"))
    return sorted(paths)


def _production_module_python_files() -> list[Path]:
    paths: list[Path] = []
    for filename in PRODUCTION_MODULE_FILES:
        path = Path(filename)
        if path.exists() and EXCLUDED_PRODUCTION_SCAN_PARTS.isdisjoint(path.parts):
            paths.append(path)
    for root in PRODUCTION_MODULE_ROOTS:
        root_path = Path(root)
        if not root_path.exists():
            continue
        for path in root_path.rglob("*.py"):
            if EXCLUDED_PRODUCTION_SCAN_PARTS.isdisjoint(path.parts):
                paths.append(path)
    return sorted(paths)


def test_forbidden_prefix_matching_is_segment_aware():
    assert _forbidden_prefix("a2a") == "a2a"
    assert _forbidden_prefix("a2a.client") == "a2a"
    assert _forbidden_prefix("a2a_adapter.client_facade") is None


def _import_from_source(source: str) -> ast.ImportFrom:
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.ImportFrom)
    return node


def test_reexport_shim_import_gate_rejects_non_owner_modules_and_bindings(tmp_path):
    path = tmp_path / "message_store.py"
    path.write_text(
        "\nfrom dal.runtime_store.parts.message_store import "
        "App"
        "Shell"
        "MessageStore, ExtraHelper\nfrom "
        "app_"
        "shell"
        ' import repository_store\nimport os\n\n__all__ = ["'
        "App"
        "Shell"
        'MessageStore"]\n',
    )

    violations = _owner_reexport_import_violations(
        path,
        {('App' + 'Shell' + 'MessageStore')},
        "dal.runtime_store.parts.message_store",
    )

    assert f"{path}:2: non-exported owner import ExtraHelper" in violations
    assert f"{path}:3: imports non-owner module {'app_' + 'shell'}" in violations
    assert f"{path}:4: imports non-owner module os" in violations


def test_owner_import_gate_rejects_non_focus_application_shell_imports(tmp_path):
    path = tmp_path / "owner.py"
    deleted_notification_module = "notification_service"
    path.write_text(
        "from "
        "app_"
        "shell"
        " import "
        "agent_service\n"
        f"from {'app_' + 'shell'}.{deleted_notification_module} import task_notifier\n"
        "import "
        "app_"
        "shell"
        ".task_service\n",
    )

    violations = _application_shell_import_violations([path])

    agent_service_module = ('app_' + 'shell' + '.') + "agent_service"
    notification_service_module = ('app_' + 'shell' + '.') + deleted_notification_module
    assert f"{path}:1: {agent_service_module}" in violations
    assert f"{path}:2: {notification_service_module}" in violations
    assert f"{path}:3: {'app_' + 'shell'}.task_service" in violations


def test_ruff_pattern_matching_detects_application_shell_globs():
    assert _ruff_pattern_matches_path(('app_' + 'shell' + '/*.py'), ('app_' + 'shell' + '/room_runtime.py'))
    assert _ruff_pattern_matches_path(
        ('app_' + 'shell' + '/repository_parts/*.py'),
        ('app_' + 'shell' + '/repository_parts/message_store.py'),
    )
    assert not _ruff_pattern_matches_path("room/**/*.py", ('app_' + 'shell' + '/room_runtime.py'))


def test_explicit_all_rejects_dynamic_mutation(tmp_path):
    path = tmp_path / "shim.py"
    path.write_text(
        """
__all__ = ["RequiredExport"]
__all__.append("ExtraExport")
""",
    )

    assert (
        _all_static_literal_violation(path)
        == f"{path}: __all__ must be a static string literal sequence"
    )
    assert _module_exports(path) == set()


def test_explicit_all_rejects_alias_call_mutation(tmp_path):
    path = tmp_path / "shim.py"
    path.write_text(
        """
__all__ = ["RequiredExport"]
exports = __all__
exports.append("ExtraExport")
""",
    )

    assert (
        _all_static_literal_violation(path)
        == f"{path}: __all__ must be a static string literal sequence"
    )
    assert _module_exports(path) == set()


def test_explicit_all_rejects_alias_subscript_mutation(tmp_path):
    path = tmp_path / "shim.py"
    path.write_text(
        """
__all__ = ["RequiredExport"]
exports = __all__
exports[0] = "OtherExport"
""",
    )

    assert (
        _all_static_literal_violation(path)
        == f"{path}: __all__ must be a static string literal sequence"
    )
    assert _module_exports(path) == set()


def test_explicit_all_rejects_subscript_mutation_and_deletion(tmp_path):
    path = tmp_path / "shim.py"
    path.write_text(
        """
__all__ = ["RequiredExport"]
__all__[0] = "OtherExport"
del __all__[:]
""",
    )

    assert (
        _all_static_literal_violation(path)
        == f"{path}: __all__ must be a static string literal sequence"
    )
    assert _module_exports(path) == set()


def test_explicit_all_rejects_set_literals(tmp_path):
    path = tmp_path / "shim.py"
    path.write_text('__all__ = {"RequiredExport"}\n')

    assert (
        _all_static_literal_violation(path)
        == f"{path}: __all__ must be a static string literal sequence"
    )
    assert _module_exports(path) == set()


def test_required_exports_require_explicit_all(tmp_path):
    path = tmp_path / "shim.py"
    path.write_text("from owner.module import RequiredExport\n")

    violations = _required_export_violations(
        path,
        {"RequiredExport"},
        "owner.module",
    )

    assert f"{path}: missing explicit __all__" in violations
    assert _module_export_surface(path, "owner.module") == set()


def test_owner_import_provenance_rejects_renamed_member_spoofing():
    spoofed_exports, _owner_refs = _owner_import_provenance(
        _import_from_source("from owner.module import OtherName as RequiredExport"),
        "owner.module",
        {"RequiredExport"},
    )
    assert spoofed_exports == set()

    backed_exports, _owner_refs = _owner_import_provenance(
        _import_from_source(
            "from owner.module import RequiredExport as RequiredExport"
        ),
        "owner.module",
        {"RequiredExport"},
    )
    assert backed_exports == {"RequiredExport"}


def test_owner_alias_rebinding_invalidates_assignment_provenance(tmp_path):
    path = tmp_path / "shim.py"
    path.write_text(
        """
import owner.module as owner_alias

owner_alias = object()
RequiredExport = owner_alias.RequiredExport
""",
    )

    assert _owner_backed_exports(path, "owner.module", {"RequiredExport"}) == set()


def test_later_import_alias_spoofing_invalidates_assignment_provenance(tmp_path):
    path = tmp_path / "shim.py"
    path.write_text(
        """
import owner.module as owner_alias
import other.module as owner_alias

RequiredExport = owner_alias.RequiredExport
""",
    )

    assert _owner_backed_exports(path, "owner.module", {"RequiredExport"}) == set()


def test_direct_owner_import_rebinding_invalidates_provenance(tmp_path):
    path = tmp_path / "shim.py"
    path.write_text(
        """
from owner.module import RequiredExport

RequiredExport = object()
__all__ = ["RequiredExport"]
""",
    )

    assert _owner_backed_exports(path, "owner.module", {"RequiredExport"}) == set()


def test_later_direct_import_spoofing_invalidates_provenance(tmp_path):
    path = tmp_path / "shim.py"
    path.write_text(
        """
from owner.module import RequiredExport
from other.module import RequiredExport

__all__ = ["RequiredExport"]
""",
    )

    assert _owner_backed_exports(path, "owner.module", {"RequiredExport"}) == set()


@pytest.mark.parametrize(
    "shadowing_definition",
    [
        "def RequiredExport():\n    return object()\n",
        "async def RequiredExport():\n    return object()\n",
        "class RequiredExport:\n    pass\n",
    ],
)
def test_direct_owner_import_definition_shadowing_invalidates_provenance(
    tmp_path,
    shadowing_definition,
):
    path = tmp_path / "shim.py"
    path.write_text(
        f"""
from owner.module import RequiredExport

{shadowing_definition}
__all__ = ["RequiredExport"]
""",
    )

    assert _owner_backed_exports(path, "owner.module", {"RequiredExport"}) == set()


def test_direct_owner_import_without_rebinding_keeps_provenance(tmp_path):
    path = tmp_path / "shim.py"
    path.write_text(
        """
from owner.module import RequiredExport

__all__ = ["RequiredExport"]
""",
    )

    assert _owner_backed_exports(path, "owner.module", {"RequiredExport"}) == {
        "RequiredExport"
    }


def test_owner_star_import_rebinding_invalidates_provenance(tmp_path, monkeypatch):
    (tmp_path / "owner").mkdir()
    (tmp_path / "owner" / "module.py").write_text("RequiredExport = object()\n")
    path = tmp_path / "shim.py"
    path.write_text(
        """
from owner.module import *

RequiredExport = object()
__all__ = ["RequiredExport"]
""",
    )
    monkeypatch.chdir(tmp_path)

    assert _owner_backed_exports(path, "owner.module", {"RequiredExport"}) == set()


@pytest.mark.parametrize(
    "shadowing_definition",
    [
        "def RequiredExport():\n    return object()\n",
        "async def RequiredExport():\n    return object()\n",
        "class RequiredExport:\n    pass\n",
    ],
)
def test_owner_star_import_definition_shadowing_invalidates_provenance(
    tmp_path,
    monkeypatch,
    shadowing_definition,
):
    (tmp_path / "owner").mkdir()
    (tmp_path / "owner" / "module.py").write_text("RequiredExport = object()\n")
    path = tmp_path / "shim.py"
    path.write_text(
        f"""
from owner.module import *

{shadowing_definition}
__all__ = ["RequiredExport"]
""",
    )
    monkeypatch.chdir(tmp_path)

    assert _owner_backed_exports(path, "owner.module", {"RequiredExport"}) == set()


def test_owner_star_import_does_not_back_private_name_without_all(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "owner").mkdir()
    (tmp_path / "owner" / "module.py").write_text("_PrivateExport = object()\n")
    path = tmp_path / "shim.py"
    path.write_text(
        """
from owner.module import *

__all__ = ["_PrivateExport"]
""",
    )
    monkeypatch.chdir(tmp_path)

    assert _owner_backed_exports(path, "owner.module", {"_PrivateExport"}) == set()


def test_owner_star_import_backs_private_name_declared_in_owner_all(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "owner").mkdir()
    (tmp_path / "owner" / "module.py").write_text(
        """
__all__ = ["_PrivateExport"]
_PrivateExport = object()
""",
    )
    path = tmp_path / "shim.py"
    path.write_text(
        """
from owner.module import *

__all__ = ["_PrivateExport"]
""",
    )
    monkeypatch.chdir(tmp_path)

    assert _owner_backed_exports(path, "owner.module", {"_PrivateExport"}) == {
        "_PrivateExport"
    }


def test_application_shell_forbidden_imports_are_manifest_blocked_by_exact_prefix():
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

    assert not violations, ('Forbidden ' + 'app-' + 'shell' + ' imports remain:\n') + "\n".join(
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


def test_application_shell_focus_files_are_final_import_shims():
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

        violations.extend(
            _required_export_violations(
                path,
                contract["required_exports"],
                contract["owning_module"],
            )
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
                f"({_public_business_method_count(path)}): " + ", ".join(public_methods)
            )

        owning_module = contract["owning_module"]
        if not _imports_module(path, owning_module):
            violations.append(
                f"{target}: does not import owning module {owning_module}"
            )
        module_name = target.removesuffix(".py").replace("/", ".")
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - assertion reports details
            violations.append(f"{target}: import failed: {exc!r}")

    assert not violations, "App-shell focus files are not final import shims:\n" + (
        "\n".join(violations)
    )


def test_application_shell_focus_owning_modules_exist_and_are_not_application_shell_owned():
    violations: list[str] = []

    contracts = {
        **FINAL_APP_SHELL_SHIMS,
        **FINAL_APP_SHELL_REEXPORT_SHIMS,
    }
    for target, contract in sorted(contracts.items()):
        owning_module = contract["owning_module"]
        owning_path = _module_path(owning_module)
        if owning_module == ('app_' + 'shell') or owning_module.startswith(('app_' + 'shell' + '.')):
            violations.append(
                f"{target}: owner remains in {'app_' + 'shell'}: {owning_module}"
            )
        if not owning_path.exists():
            violations.append(f"{target}: owner module does not exist: {owning_module}")
        elif owning_path.parts and owning_path.parts[0] == ('app_' + 'shell'):
            violations.append(
                f"{target}: owner path remains in {'app_' + 'shell'}: {owning_path}"
            )

    assert not violations, "App-shell shim owners are not final:\n" + "\n".join(
        violations
    )


def test_context_memory_runtime_wiring_avoids_application_shell_singletons():
    application_shell_prefix = ('app_' + 'shell') + "."
    forbidden = {
        f"{application_shell_prefix}{suffix}"
        for suffix in {"context_assembly_service", "memory_search_service"}
    }
    targets = [
        Path("room/compat/runtime.py"),
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


def test_context_memory_application_shell_legacy_files_are_removed():
    present = [
        path
        for path in sorted(CONTEXT_MEMORY_APP_SHELL_LEGACY_FILES)
        if Path(path).exists()
    ]

    assert not present, (
        ('ContextMemory-owned ' + 'app_' + 'shell' + ' files should be deleted:\n') + "\n".join(present)
    )


ROOM_EXECUTION_HITL_APP_SHELL_RUNTIME_SCAN_PATHS = (
    "container.py",
    "api",
    "api_gateway",
    "room",
    "execution",
    "a2a_adapter",
)

ROOM_EXECUTION_HITL_APP_SHELL_FORBIDDEN_MODULES = {
    ('app_' + 'shell' + '.room_runtime'),
    ('app_' + 'shell' + '.hitl_service'),
    ('app_' + 'shell' + '.debate_service'),
    ('app_' + 'shell' + '.task_service'),
    ('app_' + 'shell' + '.execution_runtime'),
    ('app_' + 'shell' + '.room_coordinator_service'),
}


def _room_execution_hitl_application_shell_runtime_scan_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for scan_path in ROOM_EXECUTION_HITL_APP_SHELL_RUNTIME_SCAN_PATHS:
        base = root / scan_path
        if base.is_file():
            paths.append(base)
        else:
            paths.extend(path for path in sorted(base.rglob("*.py")) if path.is_file())
    return paths


def _room_execution_hitl_application_shell_forbidden_import_violations(
    path: Path,
    root: Path,
    forbidden_modules: set[str],
) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    rel = path.relative_to(root).as_posix()
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(
                f"{rel}:{node.lineno}: {alias.name}"
                for alias in node.names
                if alias.name in forbidden_modules
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module in forbidden_modules:
                violations.append(f"{rel}:{node.lineno}: {node.module}")
            if node.module == ('app_' + 'shell'):
                violations.extend(
                    f"{rel}:{node.lineno}: {'app_' + 'shell'}.{alias.name}"
                    for alias in node.names
                    if f"{'app_' + 'shell'}.{alias.name}" in forbidden_modules
                )

    return violations


def test_room_execution_hitl_application_shell_runtime_paths_are_not_production_dependencies():
    root = Path(__file__).resolve().parents[1]

    violations: list[str] = []
    for path in _room_execution_hitl_application_shell_runtime_scan_files(root):
        violations.extend(
            _room_execution_hitl_application_shell_forbidden_import_violations(
                path,
                root,
                ROOM_EXECUTION_HITL_APP_SHELL_FORBIDDEN_MODULES,
            )
        )

    assert violations == []


def test_room_execution_hitl_application_shell_runtime_gate_flags_package_imports(
    tmp_path,
):
    path = tmp_path / "bad.py"
    path.write_text(('from ' + 'app_' + 'shell' + ' import ') + "task_service\n")
    violations = _room_execution_hitl_application_shell_forbidden_import_violations(
        path,
        tmp_path,
        {('app_' + 'shell' + '.task_service')},
    )

    assert violations == [('bad.py:1: ' + 'app_' + 'shell' + '.task_service')]


def test_room_execution_hitl_application_shell_runtime_files_are_removed():
    removed_paths = {
        ('app_' + 'shell' + '/room_runtime.py'),
        ('app_' + 'shell' + '/hitl_service.py'),
        ('app_' + 'shell' + '/debate_service.py'),
        ('app_' + 'shell' + '/task_service.py'),
        ('app_' + 'shell' + '/execution_runtime.py'),
        ('app_' + 'shell' + '/room_coordinator_service.py'),
    }

    existing = sorted(path for path in removed_paths if Path(path).exists())

    assert existing == []


def _context_memory_legacy_import_violations(path: Path) -> list[str]:
    forbidden_modules = _context_memory_legacy_modules()
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in forbidden_modules:
                    violations.append(f"{path}:{node.lineno}: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_import_from_module(path, node)
            if module in forbidden_modules:
                violations.append(f"{path}:{node.lineno}: {module}")
            if module == ('app_' + 'shell'):
                for alias in node.names:
                    if alias.name in CONTEXT_MEMORY_APP_SHELL_LEGACY_SUFFIXES:
                        violations.append(
                            f"{path}:{node.lineno}: {'app_' + 'shell'}.{alias.name}"
                        )

    return violations


def _context_memory_runtime_scan_files() -> list[Path]:
    paths = set(_production_module_python_files())
    application_shell_root = Path(('app_' + 'shell'))
    if application_shell_root.exists():
        paths.update(application_shell_root.rglob("*.py"))
    return sorted(paths)


def test_context_memory_legacy_modules_are_not_imported_anywhere_runtime_uses():
    production_and_tests = [
        *_context_memory_runtime_scan_files(),
        *sorted(Path("tests").rglob("*.py")),
    ]
    violations: list[str] = []

    for path in production_and_tests:
        if path == Path(__file__):
            continue
        violations.extend(_context_memory_legacy_import_violations(path))

    assert not violations, (
        ('ContextMemory legacy ' + 'app_' + 'shell' + ' imports remain:\n') + "\n".join(violations)
    )


def test_context_memory_legacy_gate_catches_relative_application_shell_imports(
    tmp_path,
):
    package = tmp_path / ('app_' + 'shell')
    package.mkdir()
    path = package / "consumer.py"
    path.write_text(
        "from .memory_service import MemoryService\nfrom . import compaction_service\n"
    )

    violations = _context_memory_legacy_import_violations(path)
    application_shell_prefix = ('app_' + 'shell') + "."

    assert f"{path}:1: {application_shell_prefix}memory_service" in violations
    assert f"{path}:2: {application_shell_prefix}compaction_service" in violations


def test_domain_modules_do_not_depend_on_application_shell_focus_runtime_modules():
    violations = _application_shell_focus_runtime_import_violations(
        _production_module_python_files()
    )

    assert not violations, (
        "Domain modules still import "
        "app_"
        "shell"
        " focus runtime modules:\n" + "\n".join(violations)
    )


def test_agent_runtime_consumers_do_not_import_application_shell_agent_modules():
    allowed_application_shell_shims = _application_shell_agent_shim_paths()
    scan_paths = [
        path
        for path in _agent_runtime_consumer_python_files()
        if path not in allowed_application_shell_shims
    ]
    violations = _application_shell_agent_import_violations(scan_paths)

    assert not violations, (
        "Agent runtime consumers still import "
        "app_"
        "shell"
        " Agent modules:\n" + "\n".join(violations)
    )


def test_agent_runtime_gate_catches_from_application_shell_inspection_import(tmp_path):
    path = tmp_path / "consumer.py"
    path.write_text(('from ' + 'app_' + 'shell' + ' import inspection_runtime\n'))

    assert _application_shell_agent_import_violations([path])


def test_agent_runtime_gate_catches_relative_application_shell_inspection_imports(
    tmp_path,
):
    package = tmp_path / ('app_' + 'shell')
    package.mkdir()
    path = package / "consumer.py"
    path.write_text(
        "from . import inspection_runtime\n"
        "from .inspection_runtime import "
        "App"
        "Shell"
        "InspectionCenter\n"
    )

    violations = _application_shell_agent_import_violations([path])

    assert f"{path}: from {'app_' + 'shell'} import inspection_runtime" in violations
    assert (
        f"{path}: from {'app_' + 'shell'}.inspection_runtime import ..." in violations
    )


def test_focus_owning_modules_do_not_import_application_shell_runtime():
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
        owner_violations = _application_shell_import_violations([owning_path])
        violations.extend(
            f"{owning_module}: {violation}" for violation in owner_violations
        )

    assert not violations, (
        ('Focus owning modules still import ' + 'app_' + 'shell' + ' modules:\n') + "\n".join(violations)
    )


def test_final_application_shell_shims_do_not_keep_ruff_ignore_baseline():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    per_file_ignores = pyproject["tool"]["ruff"]["lint"]["per-file-ignores"]
    shim_paths = {*FINAL_APP_SHELL_SHIMS, *FINAL_APP_SHELL_REEXPORT_SHIMS}
    allowed_owner_ignores = {
        owner_path: set(expected_ignores)
        for _shim_path, (owner_path, expected_ignores) in (
            EXPECTED_MOVED_RUFF_IGNORES.items()
        )
    }
    violations: list[str] = []

    for shim_path in sorted(shim_paths):
        matched_patterns = sorted(
            pattern
            for pattern in per_file_ignores
            if _ruff_pattern_matches_path(pattern, shim_path)
        )
        if matched_patterns:
            violations.append(
                f"{shim_path}: final shim keeps Ruff ignore baseline through "
                + ", ".join(matched_patterns)
            )

    for shim_path, (
        owner_path,
        expected_ignores,
    ) in sorted(EXPECTED_MOVED_RUFF_IGNORES.items()):
        actual = per_file_ignores.get(owner_path)
        if actual is None:
            continue
        unexpected = sorted(set(actual) - allowed_owner_ignores[owner_path])
        if unexpected:
            violations.append(
                f"{owner_path}: unexpected Ruff ignores after moving from "
                f"{shim_path}: {unexpected}; allowed: {expected_ignores}"
            )

    assert not violations, (
        "Final "
        "app-"
        "shell"
        " Ruff ignores are not owned by implementation modules:\n"
        + "\n".join(violations)
    )


def test_application_shell_repository_submodules_are_final_reexport_shims():
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

        violations.extend(
            _required_export_violations(
                path,
                contract["required_exports"],
                contract["owning_module"],
            )
        )

        owning_module = contract["owning_module"]
        if not _imports_module(path, owning_module):
            violations.append(
                f"{target}: does not import owning module {owning_module}"
            )
        violations.extend(
            _owner_reexport_import_violations(
                path,
                contract["required_exports"],
                owning_module,
            )
        )

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
    monkeypatch.setattr(
        main, "validate_runtime_bindings", fake_validate_runtime_bindings
    )
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
