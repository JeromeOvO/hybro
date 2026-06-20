import ast
import inspect
from pathlib import Path

from execution import ports

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


def test_execution_shell_ports_use_named_method_contracts() -> None:
    port_methods = {
        ports.DebateServicePort: ["inject_short_debate_for_agent_message"],
        ports.NotificationServicePort: ["send_task_update"],
        ports.RateLimitPort: ["check_rate_limit", "record_request"],
        ports.RoomMemoryPort: ["add_agent_response_to_memory"],
        ports.RoomRuntimePort: [
            "create_agent_message",
            "process_agent_message",
            "update_agent_message_by_message_id",
        ],
        ports.SSEDeliveryPort: [
            "send_task_submitted",
            "send_task_update",
            "send_rate_limit_error",
            "send_agent_response",
            "send_error",
        ],
    }

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

    assert not variadic_methods, "Port methods must use named signatures:\n" + "\n".join(
        variadic_methods
    )


def _parameter_names(obj) -> set[str]:
    return set(inspect.signature(obj.__init__).parameters)


def test_execution_runtime_constructors_do_not_use_shell_dependency_names() -> None:
    from execution.cancellation import AgentTaskCleanupAdapter, CancellationStateC3Adapter
    from execution.hitl.adapters import A2AHITLContinuationAdapter, HITLPersistenceAdapter
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

    assert not violations, "Execution constructors must use execution port names:\n" + "\n".join(violations)


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

    assert not bad, "Execution modules must use focused runtime ports:\n" + "\n".join(bad)
