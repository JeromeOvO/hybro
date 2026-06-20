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
        ports.RoomMemoryPort: [
            "add_agent_response_to_memory",
            "add_synthesis_to_history",
            "update_room_summary",
        ],
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

    assert not variadic_methods, "Port methods must use named signatures:\n" + "\n".join(
        variadic_methods
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
            "(self, room_id: 'str', user_message_id: 'str', "
            "synthesis_text: 'str') -> 'str | None'"
        ),
        (ports.RoomMemoryPort, "update_room_summary"): (
            "(self, room_id: 'str') -> 'None'"
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

    assert not bad, "Execution modules must use focused runtime ports:\n" + "\n".join(bad)
