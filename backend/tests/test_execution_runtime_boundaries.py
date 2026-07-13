import ast
import inspect
from pathlib import Path

from execution import ports

ROOT = Path(__file__).resolve().parents[1]
REMOVED_RUNTIME_PACKAGE = "app_" + "shell"


def test_execution_modules_do_not_import_removed_runtime_package() -> None:
    bad: list[str] = []
    for path in sorted((ROOT / "execution").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel_path = path.relative_to(ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == REMOVED_RUNTIME_PACKAGE or node.module.startswith(
                    f"{REMOVED_RUNTIME_PACKAGE}."
                ):
                    bad.append(f"{rel_path}:{node.lineno}:{node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == REMOVED_RUNTIME_PACKAGE or alias.name.startswith(
                        f"{REMOVED_RUNTIME_PACKAGE}."
                    ):
                        bad.append(f"{rel_path}:{node.lineno}:{alias.name}")

    assert not bad, "Execution must depend on module-owned ports:\n" + "\n".join(bad)


def test_execution_runtime_ports_use_named_method_contracts() -> None:
    port_methods = {
        ports.A2ATransportPort: [
            "has_streaming_capability",
            "send_message_streaming",
            "send_message_sync",
            "send_message_to_tracked_agent",
            "create_task_for_tracking",
            "cancel_remote_task",
            "has_push_notification_capability",
        ],
        ports.DebateServicePort: ["inject_short_debate_for_agent_message"],
        ports.CoordinatorSynthesisPort: ["emit_synthesis_message"],
        ports.A2ATaskTrackingStorePort: [
            "check_task_limits",
            "generate_webhook_token",
            "hash_webhook_token",
            "enable_task_tracking_on_message",
            "get_room_agent_message_by_message_id",
            "update_webhook_token_hash_on_message",
            "get_agent_by_agent_id",
            "update_task_on_message",
        ],
        ports.ExecutionDeliveryPort: [
            "send_task_submitted",
            "send_task_update",
            "send_rate_limit_error",
            "send_agent_response",
            "send_artifact_update",
            "send_error",
            "clear_cancellation",
            "get_token",
            "create_token",
            "remove_token",
        ],
        ports.NotificationServicePort: ["send_task_update"],
        ports.RateLimitPort: ["check_rate_limit", "record_request"],
        ports.QuotedSnippetReaderPort: ["get_quoted_snippet_by_id"],
        ports.RemoteTaskReaderPort: ["get_task_from_agent"],
        ports.RoomMemoryPort: ["add_synthesis_to_history", "update_room_summary"],
        ports.RoomRuntimePort: [
            "create_agent_message",
            "process_agent_message",
            "update_agent_message_by_message_id",
            "inquiry_agent_messages_by_related_message_id",
        ],
        ports.RoomMessageReader: [
            "get_room_user_message_by_message_id",
            "get_room_agent_message_by_message_id",
            "get_room_agent_messages_by_related_message_id",
            "get_quoted_snippet_by_id",
        ],
        ports.RoomMessageWriter: [
            "add_room_agent_message",
            "update_room_user_message_by_message_id",
            "update_room_agent_message_by_message_id",
            "update_room_agent_message_with_new_message_content_by_message_id",
            "upsert_room_agent_message",
            "delete_room_agent_message_by_message_id",
            "cancel_agent_messages_by_ids",
            "cancel_descendants",
            "claim_user_message_for_processing",
            "claim_or_reclaim_user_message",
            "refresh_processing_claim",
            "unclaim_user_message",
            "turn_exists",
            "accumulate_artifact_on_message",
            "update_last_notified_state",
            "reset_last_notified_state",
            "update_task_state_on_message",
        ],
        ports.RoomTaskStateStore: [
            "resolve_client_request_id_for_message_id",
            "resolve_client_request_id_for_agent_message",
            "enable_task_tracking_on_message",
            "update_task_on_message",
            "is_message_cancelled",
        ],
        ports.RoomContinuationStore: [
            "get_pending_continuation_on_message",
            "get_and_clear_continuation_on_message",
            "get_and_clear_continuation_on_user_message",
            "save_continuation_on_message",
            "save_continuation_on_user_message",
        ],
        ports.RoomReader: [
            "get_room_by_room_id",
            "get_agent_by_agent_id",
            "get_agent_name_by_agent_id",
            "get_agent_group_by_id",
        ],
        ports.RoomWriter: ["update_room_by_room_id"],
        ports.RoomMemoryReader: ["get_room_memory_by_room_id"],
        ports.HITLReaderPort: ["get_pending_hitl_requests_for_message"],
    }

    assert ports.RoomCoordinatorPort is ports.CoordinatorSynthesisPort
    assert ports.SSEDeliveryPort is ports.ExecutionDeliveryPort

    variadic_methods: list[str] = []
    for port, method_names in port_methods.items():
        for method_name in method_names:
            signature = inspect.signature(getattr(port, method_name))
            if any(
                parameter.kind
                in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                )
                for parameter in signature.parameters.values()
            ):
                variadic_methods.append(f"{port.__name__}.{method_name}{signature}")

    assert not variadic_methods, (
        "Port methods must use named signatures:\n" + "\n".join(variadic_methods)
    )


def test_execution_focused_port_signatures_match_plan() -> None:
    expected_signatures = {
        (ports.A2ATransportPort, "has_streaming_capability"): (
            "(self, *, agent_card: 'AgentCard') -> 'bool'"
        ),
        (ports.A2ATransportPort, "send_message_streaming"): (
            "(self, agent_card: 'AgentCard', message: 'Any', *, "
            "agent_id: 'str | None' = None) -> 'AsyncIterator[Any]'"
        ),
        (ports.A2ATransportPort, "send_message_sync"): (
            "(self, *, agent_card: 'AgentCard', message: 'Any', "
            "agent_id: 'str | None' = None) -> 'Any'"
        ),
        (ports.A2ATransportPort, "send_message_to_tracked_agent"): (
            "(self, *, agent_card: 'AgentCard', message: 'Any', "
            "message_id: 'str', webhook_token: 'str', context_id: 'str', "
            "agent_id: 'str | None' = None) -> 'dict[str, Any]'"
        ),
        (ports.A2ATransportPort, "create_task_for_tracking"): (
            "(self, current_message: 'RoomAgentMessage', agent_card: 'AgentCard', "
            "prepared_message: 'Any', *, step_number: 'int | None' = None, "
            "total_steps: 'int | None' = None) -> 'dict[str, Any]'"
        ),
        (ports.A2ATransportPort, "cancel_remote_task"): (
            "(self, agent_card: 'AgentCard', remote_task_id: 'str') -> 'None'"
        ),
        (ports.A2ATransportPort, "has_push_notification_capability"): (
            "(self, agent_card: 'AgentCard') -> 'bool'"
        ),
        (ports.RemoteTaskReaderPort, "get_task_from_agent"): (
            "(self, agent_card: 'AgentCard', task_id: 'str', *, "
            "agent_id: 'str | None' = None) -> 'Any'"
        ),
        (ports.A2ATaskTrackingStorePort, "check_task_limits"): (
            "(self, user_id: 'str', room_id: 'str', "
            "non_terminal_state_values: 'list[str]') -> 'None'"
        ),
        (ports.RoomMemoryPort, "add_synthesis_to_history"): (
            "(self, room_id: 'str', synthesis_text: 'str', "
            "trajectory: 'Any | None' = None) -> 'str | None'"
        ),
        (ports.RoomMemoryPort, "update_room_summary"): (
            "(self, room_id: 'str', synthesis_text: 'str', "
            "synthesis_turn_id: 'str | None' = None) -> 'bool'"
        ),
        (ports.RoomRuntimePort, "inquiry_agent_messages_by_related_message_id"): (
            "(self, related_message_id: 'str') -> 'Any'"
        ),
        (ports.RoomMessageWriter, "accumulate_artifact_on_message"): (
            "(self, message_id: 'str', artifact: 'dict[str, Any]', *, "
            "append: 'bool' = False) -> 'bool'"
        ),
        (ports.ExecutionDeliveryPort, "send_artifact_update"): (
            "(self, *, room_id: 'str', message_id: 'str', agent_id: 'str', "
            "artifact: 'dict[str, Any]', append: 'bool' = False, "
            "last_chunk: 'bool' = False, "
            "client_request_id: 'str | None' = None) -> 'None'"
        ),
    }

    mismatches = []
    for (port, method_name), expected in expected_signatures.items():
        actual = str(inspect.signature(getattr(port, method_name)))
        if actual != expected:
            mismatches.append(f"{port.__name__}.{method_name}: {actual} != {expected}")

    assert not mismatches, "Port signatures must match the plan:\n" + "\n".join(
        mismatches
    )


def _parameter_names(obj) -> set[str]:
    return set(inspect.signature(obj.__init__).parameters)


FORBIDDEN_CONSTRUCTOR_PARAMETER_NAMES = {
    "a2a_service",
    "database_service",
    "db_service",
    "room_coordinator_service",
    "room_memory_service",
    "room_services",
    "sse_manager",
    "store",
    "task_service",
    "task_store",
}


def test_execution_runtime_constructors_do_not_use_shell_dependency_names() -> None:
    from execution.cancellation import (
        AgentTaskCleanupAdapter,
        CancellationStateC3Adapter,
    )
    from execution.hitl.adapters import (
        A2AHITLContinuationAdapter,
        HITLPersistenceAdapter,
    )
    from execution.orchestration.queue_executor import QueueExecutor
    from execution.orchestration.room_message_center import RoomMessageCenter
    from execution.orchestration.supervisor_executor import SupervisorExecutor
    from execution.state.task_state_manager import TaskStateManager
    from execution.task_tracking import A2ATaskTrackingService

    forbidden_by_class = {
        A2AHITLContinuationAdapter: {
            "a2a_service",
        },
        A2ATaskTrackingService: {
            "task_store",
        },
        AgentTaskCleanupAdapter: {
            "store",
            "database_service",
            "db_service",
        },
        CancellationStateC3Adapter: {
            "sse_manager",
        },
        HITLPersistenceAdapter: {
            "store",
            "database_service",
            "db_service",
        },
        RoomMessageCenter: {
            "room_services",
            "store",
            "sse_manager",
            "room_coordinator_service",
            "a2a_service",
            "room_memory_service",
            "database_service",
            "db_service",
            "task_service",
        },
        QueueExecutor: {
            "room_services",
            "store",
            "sse_manager",
            "a2a_service",
            "room_memory_service",
        },
        SupervisorExecutor: {
            "coordinator",
            "room_services",
            "store",
            "sse_manager",
            "room_coordinator_service",
            "room_memory_service",
        },
        TaskStateManager: {
            "room_services",
        },
    }

    violations: list[str] = []
    for cls, forbidden_names in forbidden_by_class.items():
        found = sorted(_parameter_names(cls) & forbidden_names)
        for name in found:
            violations.append(f"{cls.__name__}.__init__ parameter {name!r}")

    for path in sorted((ROOT / "execution").rglob("*.py")):
        rel_path = path.relative_to(ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for body_node in node.body:
                if not (
                    isinstance(body_node, ast.FunctionDef)
                    and body_node.name == "__init__"
                ):
                    continue
                parameters = [
                    *body_node.args.posonlyargs,
                    *body_node.args.args,
                    *body_node.args.kwonlyargs,
                ]
                found = sorted(
                    parameter.arg
                    for parameter in parameters
                    if parameter.arg != "self"
                    and parameter.arg in FORBIDDEN_CONSTRUCTOR_PARAMETER_NAMES
                )
                for name in found:
                    violations.append(
                        f"{rel_path}:{body_node.lineno}: "
                        f"{node.name}.__init__ parameter {name!r}"
                    )

    assert not violations, (
        "Execution constructors must use execution port names:\n"
        + "\n".join(violations)
    )


def test_execution_modules_do_not_store_legacy_runtime_fields() -> None:
    bad: list[str] = []
    forbidden_attrs = {
        "_a2a_service",
        "_db",
        "_sse",
        "_sse_manager",
        "_store",
        "_task_store",
        "a2a_service",
        "database_service",
        "room_coordinator_service",
        "room_memory_service",
        "room_services",
        "sse_manager",
    }
    forbidden_tokens = [
        "self._store",
        "self._db",
        "self._sse",
        "self._task_store",
        "task_store: Any",
        "_sse_manager",
        "_a2a_service",
        "_collect_agent_messages_for_user_message",
        "database_service",
        "db_service",
        "sse_manager",
        "a2a_service",
        "task_service",
        "room_services",
        "room_memory_service",
        "room_coordinator_service",
    ]
    for path in sorted((ROOT / "execution").rglob("*.py")):
        rel_path = path.relative_to(ROOT)
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
                bad.append(f"{rel_path}:{node.lineno}: {ast.unparse(node)}")
        for token in forbidden_tokens:
            if token in source:
                bad.append(f"{rel_path}: contains {token!r}")

    assert not bad, "Execution modules must use focused runtime ports:\n" + "\n".join(
        bad
    )


FORBIDDEN_CONTROL_PLANE_MODULES = {
    "execution.orchestration.action_validator",
    "execution.orchestration.blocker_resolver",
    "execution.orchestration.outcome_evaluator",
    "execution.orchestration.outcome_policy",
    "execution.orchestration.planner_recovery",
    "execution.orchestration.recovery_policy",
    "execution.orchestration.run_reducer",
    "execution.orchestration.run_store",
    "execution.orchestration.supervisor_executor",
    "execution.orchestration.terminal_summary",
    "models.orchestration",
}

FORBIDDEN_CONTROL_PLANE_SYMBOLS = {
    "BlockerPolicyValidator",
    "DelegationOutcomeEvaluator",
    "OrchestrationRunState",
    "OrchestrationStatus",
    "PlannerAction",
    "PlannerActionType",
    "PlannerActionValidator",
    "PlannerActionValidationError",
    "SupervisorExecutor",
    "apply_recoverable_planner_rejection",
    "build_terminal_summary",
    "mark_terminal",
    "record_agent_dispatch",
    "record_agent_observation",
    "record_planner_action",
    "resolve_agent_observed_blockers",
    "validate_hitl_answered_blockers",
}

ROOM_MESSAGE_CENTER_FORBIDDEN_POLICY_MODULES = {
    "execution.orchestration.action_validator",
    "execution.orchestration.blocker_resolver",
    "execution.orchestration.outcome_evaluator",
    "execution.orchestration.outcome_policy",
    "execution.orchestration.planner_recovery",
    "execution.orchestration.recovery_policy",
    "execution.orchestration.run_reducer",
    "execution.orchestration.terminal_summary",
}

ROOM_MESSAGE_CENTER_FORBIDDEN_POLICY_SYMBOLS = {
    "BlockerPolicyValidator",
    "DelegationOutcomeEvaluator",
    "PlannerActionValidator",
    "PlannerActionValidationError",
    "apply_recoverable_planner_rejection",
    "build_terminal_summary",
    "mark_terminal",
    "record_agent_dispatch",
    "record_agent_observation",
    "record_planner_action",
    "resolve_agent_observed_blockers",
    "validate_hitl_answered_blockers",
}

ORCHESTRATION_RUN_STATE_CONTROL_FIELDS = {
    "agent_outputs",
    "blockers",
    "candidate_agent_ids",
    "candidate_scope",
    "current_step_id",
    "delegation_outcomes",
    "dispatch_intents",
    "dispatch_results",
    "facts",
    "interrupted_state",
    "open_failures",
    "planner_actions",
    "status",
    "step_budget",
    "steps_used",
    "terminal_reason",
    "terminal_summary",
    "unknowns",
}

MUTATING_COLLECTION_METHODS = {
    "add",
    "append",
    "clear",
    "discard",
    "extend",
    "insert",
    "pop",
    "popitem",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "update",
}


def _parse_source(source: str, rel_path: Path) -> ast.Module:
    return ast.parse(source, filename=str(rel_path))


def _read_source(rel_path: Path) -> str:
    return (ROOT / rel_path).read_text()


def _dotted_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        if prefix:
            return f"{prefix}.{node.attr}"
        return node.attr
    return None


def _resolve_dotted_name(name: str | None, aliases: dict[str, str]) -> str | None:
    if not name:
        return None
    parts = name.split(".")
    for length in range(len(parts), 0, -1):
        prefix = ".".join(parts[:length])
        if prefix in aliases:
            return ".".join([aliases[prefix], *parts[length:]])
    return name


def _is_forbidden_module(target: str, forbidden_modules: set[str]) -> bool:
    return any(
        target == module or target.startswith(f"{module}.")
        for module in forbidden_modules
    )


def _is_forbidden_symbol(target: str, forbidden_symbols: set[str]) -> bool:
    parts = target.split(".")
    return any(symbol in parts for symbol in forbidden_symbols)


def _is_forbidden_target(
    target: str | None,
    *,
    forbidden_modules: set[str],
    forbidden_symbols: set[str],
) -> bool:
    if not target:
        return False
    return _is_forbidden_module(target, forbidden_modules) or _is_forbidden_symbol(
        target,
        forbidden_symbols,
    )


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    root_name = alias.name.split(".", 1)[0]
                    aliases.setdefault(root_name, root_name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _import_violations(
    tree: ast.Module,
    *,
    rel_path: Path,
    forbidden_modules: set[str],
    forbidden_symbols: set[str],
) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_target(
                    alias.name,
                    forbidden_modules=forbidden_modules,
                    forbidden_symbols=forbidden_symbols,
                ):
                    violations.append(
                        f"{rel_path}:{node.lineno}: import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if _is_forbidden_module(node.module, forbidden_modules):
                violations.append(
                    f"{rel_path}:{node.lineno}: from {node.module} import ..."
                )
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported = f"{node.module}.{alias.name}"
                if _is_forbidden_target(
                    imported,
                    forbidden_modules=forbidden_modules,
                    forbidden_symbols=forbidden_symbols,
                ):
                    violations.append(
                        f"{rel_path}:{node.lineno}: from {node.module} "
                        f"import {alias.name}"
                    )
    return violations


def _constructor_bindings(
    tree: ast.Module,
    aliases: dict[str, str],
    *,
    forbidden_modules: set[str],
    forbidden_symbols: set[str],
) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        resolved = _resolve_dotted_name(_dotted_name(value.func), aliases)
        if not _is_forbidden_target(
            resolved,
            forbidden_modules=forbidden_modules,
            forbidden_symbols=forbidden_symbols,
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = resolved or target.id
    return bindings


def _control_plane_boundary_violations_for_source(
    source: str,
    *,
    rel_path: Path,
    forbidden_modules: set[str] = FORBIDDEN_CONTROL_PLANE_MODULES,
    forbidden_symbols: set[str] = FORBIDDEN_CONTROL_PLANE_SYMBOLS,
) -> list[str]:
    tree = _parse_source(source, rel_path)
    aliases = _import_aliases(tree)
    bindings = {
        **aliases,
        **_constructor_bindings(
            tree,
            aliases,
            forbidden_modules=forbidden_modules,
            forbidden_symbols=forbidden_symbols,
        ),
    }
    violations = _import_violations(
        tree,
        rel_path=rel_path,
        forbidden_modules=forbidden_modules,
        forbidden_symbols=forbidden_symbols,
    )
    seen = set(violations)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            kind = "call"
            raw = _dotted_name(node.func)
        elif isinstance(node, ast.Attribute):
            kind = "use"
            raw = _dotted_name(node)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            kind = "use"
            raw = node.id
        else:
            continue

        resolved = _resolve_dotted_name(raw, bindings)
        if not _is_forbidden_target(
            resolved,
            forbidden_modules=forbidden_modules,
            forbidden_symbols=forbidden_symbols,
        ):
            continue
        message = f"{rel_path}:{node.lineno}: {kind} {resolved}"
        if message not in seen:
            violations.append(message)
            seen.add(message)

    return violations


def _control_plane_boundary_violations(rel_path: Path) -> list[str]:
    return _control_plane_boundary_violations_for_source(
        _read_source(rel_path),
        rel_path=rel_path,
    )


def _annotation_mentions_run_state(
    annotation: ast.AST | None,
    aliases: dict[str, str],
) -> bool:
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "OrchestrationRunState" in node.value:
                return True
        raw = _dotted_name(node)
        resolved = _resolve_dotted_name(raw, aliases)
        if resolved and "OrchestrationRunState" in resolved.split("."):
            return True
    return False


def _collect_run_state_names(tree: ast.Module, aliases: dict[str, str]) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parameters = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            for parameter in parameters:
                if _annotation_mentions_run_state(parameter.annotation, aliases):
                    names.add(parameter.arg)
            if node.args.vararg and _annotation_mentions_run_state(
                node.args.vararg.annotation,
                aliases,
            ):
                names.add(node.args.vararg.arg)
            if node.args.kwarg and _annotation_mentions_run_state(
                node.args.kwarg.annotation,
                aliases,
            ):
                names.add(node.args.kwarg.arg)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and _annotation_mentions_run_state(
                node.annotation,
                aliases,
            ):
                names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            value_name = _dotted_name(node.value)
            if value_name and value_name.endswith(".run_state"):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return names


def _root_name(node: ast.AST | None) -> str | None:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    if isinstance(current, ast.Name):
        return current.id
    return None


def _is_run_state_name(name: str | None, run_state_names: set[str]) -> bool:
    return bool(
        name
        and (
            name in run_state_names
            or name == "run_state"
            or name.endswith("_run_state")
            or name == "orchestration_state"
            or name.endswith("_orchestration_state")
        )
    )


def _literal_subscript_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _run_state_control_mutation_violations_for_source(
    source: str,
    *,
    rel_path: Path,
) -> list[str]:
    tree = _parse_source(source, rel_path)
    aliases = _import_aliases(tree)
    run_state_names = _collect_run_state_names(tree, aliases)
    violations: list[str] = []

    def record_target(target: ast.AST, lineno: int) -> None:
        if isinstance(target, ast.Attribute):
            root = _root_name(target.value)
            if (
                target.attr in ORCHESTRATION_RUN_STATE_CONTROL_FIELDS
                and _is_run_state_name(root, run_state_names)
            ):
                violations.append(
                    f"{rel_path}:{lineno}: assign {_dotted_name(target)}"
                )
        elif isinstance(target, ast.Subscript):
            root = _root_name(target.value)
            field = _literal_subscript_key(target.slice)
            if (
                field in ORCHESTRATION_RUN_STATE_CONTROL_FIELDS
                and _is_run_state_name(root, run_state_names)
            ):
                violations.append(f"{rel_path}:{lineno}: assign {root}[{field!r}]")

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                record_target(target, node.lineno)
        elif isinstance(node, ast.AnnAssign):
            record_target(node.target, node.lineno)
        elif isinstance(node, ast.AugAssign):
            record_target(node.target, node.lineno)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                record_target(target, node.lineno)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in MUTATING_COLLECTION_METHODS:
                continue
            collection = node.func.value
            if not isinstance(collection, ast.Attribute):
                continue
            root = _root_name(collection.value)
            if (
                collection.attr in ORCHESTRATION_RUN_STATE_CONTROL_FIELDS
                and _is_run_state_name(root, run_state_names)
            ):
                violations.append(
                    f"{rel_path}:{node.lineno}: mutate "
                    f"{_dotted_name(collection)}.{node.func.attr}()"
                )

    return violations


def _run_state_control_mutation_violations(rel_path: Path) -> list[str]:
    return _run_state_control_mutation_violations_for_source(
        _read_source(rel_path),
        rel_path=rel_path,
    )


def _room_message_center_policy_helper_violations(rel_path: Path) -> list[str]:
    return _control_plane_boundary_violations_for_source(
        _read_source(rel_path),
        rel_path=rel_path,
        forbidden_modules=ROOM_MESSAGE_CENTER_FORBIDDEN_POLICY_MODULES,
        forbidden_symbols=ROOM_MESSAGE_CENTER_FORBIDDEN_POLICY_SYMBOLS,
    )


def test_boundary_ast_checks_ignore_comments_docstrings_and_string_literals() -> None:
    source = '''
"""SupervisorExecutor, OrchestrationRunState, and build_terminal_summary are prose."""

# PlannerActionValidator.validate and resolve_agent_observed_blockers are comments.

def harmless() -> str:
    return "OrchestrationStatus and DelegationOutcomeEvaluator are literal text"
'''

    assert not _control_plane_boundary_violations_for_source(
        source,
        rel_path=Path("example.py"),
    )
    assert not _run_state_control_mutation_violations_for_source(
        source,
        rel_path=Path("example.py"),
    )


def test_boundary_ast_checks_reject_aliases_calls_and_state_mutations() -> None:
    source = """
from execution.orchestration.action_validator import PlannerActionValidator as Validator
from models.orchestration import OrchestrationRunState as RunState


def bad(state: RunState) -> None:
    validator = Validator()
    validator.validate(None, run_state=state)
    state.status = "failed"
"""

    control_plane_violations = _control_plane_boundary_violations_for_source(
        source,
        rel_path=Path("example.py"),
    )
    mutation_violations = _run_state_control_mutation_violations_for_source(
        source,
        rel_path=Path("example.py"),
    )

    assert any("PlannerActionValidator" in item for item in control_plane_violations)
    assert any("OrchestrationRunState" in item for item in control_plane_violations)
    assert any(".validate" in item for item in control_plane_violations)
    assert any(".status" in item for item in mutation_violations)


def test_direct_transport_and_queue_executor_do_not_use_supervisor_control_plane() -> None:
    violations: list[str] = []
    for rel_path in (
        Path("execution/dispatch/transports/direct.py"),
        Path("execution/orchestration/queue_executor.py"),
    ):
        violations.extend(_control_plane_boundary_violations(rel_path))

    assert not violations, (
        "DirectTransport and QueueExecutor must not import or call "
        "orchestration run-state / next-step policy helpers:\n"
        + "\n".join(violations)
    )


def test_runtime_boundary_modules_do_not_mutate_run_state_control_fields() -> None:
    violations: list[str] = []
    for rel_path in (
        Path("execution/dispatch/transports/direct.py"),
        Path("execution/orchestration/queue_executor.py"),
        Path("execution/orchestration/room_message_center.py"),
    ):
        violations.extend(_run_state_control_mutation_violations(rel_path))

    assert not violations, (
        "Runtime boundary modules must not mutate orchestration run-state "
        "control fields outside supervisor paths:\n"
        + "\n".join(violations)
    )


def test_room_message_center_only_wires_supervisor_entrypoint_not_policy_helpers() -> None:
    violations = _room_message_center_policy_helper_violations(
        Path("execution/orchestration/room_message_center.py")
    )

    assert not violations, (
        "RoomMessageCenter may select and invoke SupervisorExecutor, but must "
        "not call planner/outcome/blocker/terminal policy helpers directly:\n"
        + "\n".join(violations)
    )
