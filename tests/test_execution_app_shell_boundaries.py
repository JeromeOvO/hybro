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
