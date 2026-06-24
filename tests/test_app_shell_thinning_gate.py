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
    "app_shell/repository_parts/__init__.py": {
        "max_lines": 40,
        "required_exports": {
            "AppShellAgentRoomStore",
            "AppShellHITLStore",
            "AppShellMemoryStore",
            "AppShellMessageStore",
            "AppShellTaskLifecycleStore",
        },
        "owning_module": "dal.runtime_store.parts",
    },
    "app_shell/repository_parts/agent_room_store.py": {
        "max_lines": 30,
        "required_exports": {"AppShellAgentRoomStore"},
        "owning_module": "dal.runtime_store.parts.agent_room_store",
    },
    "app_shell/repository_parts/message_store.py": {
        "max_lines": 30,
        "required_exports": {"AppShellMessageStore"},
        "owning_module": "dal.runtime_store.parts.message_store",
    },
    "app_shell/repository_parts/task_lifecycle_store.py": {
        "max_lines": 30,
        "required_exports": {"AppShellTaskLifecycleStore"},
        "owning_module": "dal.runtime_store.parts.task_lifecycle_store",
    },
    "app_shell/repository_parts/hitl_store.py": {
        "max_lines": 30,
        "required_exports": {"AppShellHITLStore"},
        "owning_module": "dal.runtime_store.parts.hitl_store",
    },
    "app_shell/repository_parts/memory_store.py": {
        "max_lines": 30,
        "required_exports": {"AppShellMemoryStore"},
        "owning_module": "dal.runtime_store.parts.memory_store",
    },
    "app_shell/repository_parts/parsing.py": {
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
    "app_shell/repository_parts/webhook_tokens.py": {
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

PRODUCTION_MODULE_FILES = ("container.py",)

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
    if len(all_values) == 1 and _static_string_literal_sequence(all_values[0]) is not None:
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


def _top_level_function(path: Path, name: str) -> ast.FunctionDef | None:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _compares_name_to_string(node: ast.Compare, name: str) -> str | None:
    if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
        return None
    if len(node.comparators) != 1:
        return None

    left = node.left
    right = node.comparators[0]
    if (
        isinstance(left, ast.Name)
        and left.id == name
        and isinstance(right, ast.Constant)
        and isinstance(right.value, str)
    ):
        return right.value
    if (
        isinstance(right, ast.Name)
        and right.id == name
        and isinstance(left, ast.Constant)
        and isinstance(left.value, str)
    ):
        return left.value
    return None


def _raises_attribute_error(node: ast.AST) -> bool:
    if not isinstance(node, ast.Raise) or node.exc is None:
        return False
    exc = node.exc
    if isinstance(exc, ast.Name):
        return exc.id == "AttributeError"
    if isinstance(exc, ast.Call):
        return isinstance(exc.func, ast.Name) and exc.func.id == "AttributeError"
    return False


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


def _invalidated_owner_refs(
    tree: ast.Module,
    owner_refs: set[tuple[str, ...]],
) -> set[tuple[str, ...]]:
    mutated_roots: set[str] = set()
    for node in tree.body:
        mutated_roots.update(_mutation_target_roots(node))
    return {ref for ref in owner_refs if ref and ref[0] in mutated_roots}


def _relay_impl_aliases(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    aliases: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "hub_runtime_bridge.compat":
            continue
        aliases.update(
            alias.asname or alias.name
            for alias in node.names
            if alias.name == "relay_service"
        )
    invalidated_aliases = {
        alias
        for alias in aliases
        if (alias,) in _invalidated_owner_refs(tree, {(alias,) for alias in aliases})
    }
    return aliases - invalidated_aliases


def _returns_relay_impl_service(node: ast.AST, relay_impl_aliases: set[str]) -> bool:
    if not isinstance(node, ast.Return) or node.value is None:
        return False
    chain = _attribute_chain(node.value)
    return chain in {(alias, "relay_service") for alias in relay_impl_aliases}


def _relay_getattr_handler_if(
    function: ast.FunctionDef,
    attr_name_arg: str,
    relay_impl_aliases: set[str],
) -> ast.If | None:
    for node in function.body:
        if not isinstance(node, ast.If):
            continue
        if not isinstance(node.test, ast.Compare):
            continue
        if _compares_name_to_string(node.test, attr_name_arg) != "relay_service":
            continue
        if len(node.body) == 1 and _returns_relay_impl_service(
            node.body[0],
            relay_impl_aliases,
        ):
            return node
    return None


def _relay_getattr_has_attribute_error_fallback(
    function: ast.FunctionDef,
    handler_if: ast.If,
) -> bool:
    handler_index = function.body.index(handler_if)
    if handler_index != 0:
        return False
    if handler_if.orelse:
        if len(function.body) != 1:
            return False
        fallback_nodes = handler_if.orelse
    else:
        fallback_nodes = function.body[handler_index + 1 :]
    if len(fallback_nodes) != 1:
        return False
    return _raises_attribute_error(fallback_nodes[0])


def _relay_getattr_is_valid(path: Path) -> bool:
    if path != Path("app_shell/relay_service.py"):
        return False
    function = _top_level_function(path, "__getattr__")
    if function is None or not function.args.args:
        return False
    relay_impl_aliases = _relay_impl_aliases(path)
    if not relay_impl_aliases:
        return False

    attr_name_arg = function.args.args[0].arg
    handler_if = _relay_getattr_handler_if(
        function,
        attr_name_arg,
        relay_impl_aliases,
    )
    if handler_if is None:
        return False
    return _relay_getattr_has_attribute_error_fallback(function, handler_if)


def _dynamic_required_export_is_allowed(path: Path, export: str) -> bool:
    return export == "relay_service" and _relay_getattr_is_valid(path)


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

    return {name for name in _module_bound_names(owner_path) if not name.startswith("_")}


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
                    required_exports
                    & _owner_static_star_import_exports(owning_module)
                )
            elif alias.name in required_exports and alias.asname in {None, alias.name}:
                backed_exports.add(alias.name)

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
            continue

        rebound_exports.update(imported_exports & _mutation_target_roots(node))

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
    imported_member = chain[-1]
    if chain[:-1] not in owner_refs:
        return set()

    backed_exports: set[str] = set()
    for target in node.targets:
        backed_exports.update(
            target_name
            for target_name in _target_names(target)
            if target_name == imported_member
        )
    return backed_exports


def _owner_backed_exports(
    path: Path,
    owning_module: str,
    required_exports: set[str],
) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    backed_exports: set[str] = set()
    owner_refs: set[tuple[str, ...]] = set()

    for node in tree.body:
        imported_exports, imported_refs = _owner_import_provenance(
            node,
            owning_module,
            required_exports,
        )
        backed_exports.update(imported_exports)
        owner_refs.update(imported_refs)

    owner_refs -= _invalidated_owner_refs(tree, owner_refs)
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
        violations.append(f"{path}: unexpected exports: {', '.join(unexpected_exports)}")
    if missing_bound_names:
        violations.append(
            f"{path}: required exports are not bound: "
            + ", ".join(missing_bound_names)
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
        and not (
            isinstance(node, ast.FunctionDef)
            and node.name == "__getattr__"
            and _relay_getattr_is_valid(path)
        )
    ]


def _imports_module(path: Path, expected_module: str) -> bool:
    for _lineno, imported_module in _import_modules(path):
        if (
            imported_module == expected_module
            or imported_module.startswith(f"{expected_module}.")
        ):
            return True
    return False


def _is_focus_module(module: str) -> bool:
    return any(
        module == focus_module or module.startswith(f"{focus_module}.")
        for focus_module in FOCUS_MODULES
    )


def _app_shell_focus_runtime_imports_for_node(
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
    if node.module == "app_shell":
        violations.extend(
            f"{path}:{node.lineno}: app_shell.{alias.name}"
            for alias in node.names
            if _is_focus_module(f"app_shell.{alias.name}")
        )
    return violations


def _app_shell_focus_runtime_import_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in sorted(paths):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            violations.extend(_app_shell_focus_runtime_imports_for_node(path, node))
    return violations


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


def _comparison_expression(source: str) -> ast.Compare:
    expression = ast.parse(source, mode="eval").body
    assert isinstance(expression, ast.Compare)
    return expression


def _function_from_source(source: str) -> ast.FunctionDef:
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    return function


def _import_from_source(source: str) -> ast.ImportFrom:
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.ImportFrom)
    return node


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


def test_relay_getattr_export_comparison_requires_equality_operator():
    assert (
        _compares_name_to_string(
            _comparison_expression('name == "relay_service"'),
            "name",
        )
        == "relay_service"
    )
    assert (
        _compares_name_to_string(
            _comparison_expression('"relay_service" == name'),
            "name",
        )
        == "relay_service"
    )
    assert (
        _compares_name_to_string(
            _comparison_expression('name != "relay_service"'),
            "name",
        )
        is None
    )
    assert (
        _compares_name_to_string(
            _comparison_expression('name is "relay_service"'),
            "name",
        )
        is None
    )


def test_relay_getattr_fallback_rejects_return_before_attribute_error():
    valid_function = _function_from_source(
        """
def __getattr__(name):
    if name == "relay_service":
        return _impl.relay_service
    raise AttributeError(name)
"""
    )
    valid_handler = _relay_getattr_handler_if(valid_function, "name", {"_impl"})
    assert valid_handler is not None
    assert _relay_getattr_has_attribute_error_fallback(valid_function, valid_handler)

    invalid_function = _function_from_source(
        """
def __getattr__(name):
    if name == "relay_service":
        return _impl.relay_service
    return None
    raise AttributeError(name)
"""
    )
    invalid_handler = _relay_getattr_handler_if(invalid_function, "name", {"_impl"})
    assert invalid_handler is not None
    assert not _relay_getattr_has_attribute_error_fallback(
        invalid_function,
        invalid_handler,
    )


def test_relay_getattr_branch_must_directly_return_owner_service():
    invalid_function = _function_from_source(
        """
def __getattr__(name):
    if name == "relay_service":
        return None
        return _impl.relay_service
    raise AttributeError(name)
"""
    )

    assert _relay_getattr_handler_if(invalid_function, "name", {"_impl"}) is None


def test_relay_impl_alias_rebinding_invalidates_dynamic_export(tmp_path):
    path = tmp_path / "relay_service.py"
    path.write_text(
        """
from hub_runtime_bridge.compat import relay_service as _impl

_impl = object()

def __getattr__(name):
    if name == "relay_service":
        return _impl.relay_service
    raise AttributeError(name)
""",
    )

    assert _relay_impl_aliases(path) == set()


def test_owner_import_provenance_rejects_renamed_member_spoofing():
    spoofed_exports, _owner_refs = _owner_import_provenance(
        _import_from_source("from owner.module import OtherName as RequiredExport"),
        "owner.module",
        {"RequiredExport"},
    )
    assert spoofed_exports == set()

    backed_exports, _owner_refs = _owner_import_provenance(
        _import_from_source("from owner.module import RequiredExport as RequiredExport"),
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
    violations = _app_shell_focus_runtime_import_violations(
        _production_module_python_files()
    )

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

        violations.extend(
            _required_export_violations(
                path,
                contract["required_exports"],
                contract["owning_module"],
            )
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
