import inspect
import subprocess
import sys
from typing import get_type_hints

from common.protocols import (
    RuntimeAgentRoomStore,
    RuntimeHITLStore,
    RuntimeMemoryStore,
    RuntimeMessageStore,
    RuntimeTaskLifecycleStore,
)


def test_runtime_store_protocols_are_exported():
    import common.protocols as protocols

    assert protocols.RuntimeAgentRoomStore is RuntimeAgentRoomStore
    assert protocols.RuntimeMessageStore is RuntimeMessageStore
    assert protocols.RuntimeTaskLifecycleStore is RuntimeTaskLifecycleStore
    assert protocols.RuntimeHITLStore is RuntimeHITLStore
    assert protocols.RuntimeMemoryStore is RuntimeMemoryStore


def test_app_shell_repository_store_declares_runtime_protocol_surface():
    from app_shell.repository_store import AppShellRepositoryStore

    store = object.__new__(AppShellRepositoryStore)

    assert isinstance(store, RuntimeAgentRoomStore)
    assert isinstance(store, RuntimeMessageStore)
    assert isinstance(store, RuntimeTaskLifecycleStore)
    assert isinstance(store, RuntimeHITLStore)
    assert isinstance(store, RuntimeMemoryStore)


def _signature_text(obj, method_name: str) -> str:
    return str(inspect.signature(getattr(obj, method_name)))


def test_runtime_store_protocol_signatures_match_current_store_surface():
    from app_shell.repository_store import AppShellRepositoryStore
    from common.protocols.runtime_store_protocols import (
        RuntimeAgentRoomStore,
        RuntimeHITLStore,
        RuntimeMemoryStore,
        RuntimeMessageStore,
        RuntimeTaskLifecycleStore,
    )

    protocol_types = [
        RuntimeAgentRoomStore,
        RuntimeMessageStore,
        RuntimeTaskLifecycleStore,
        RuntimeHITLStore,
        RuntimeMemoryStore,
    ]

    for protocol_type in protocol_types:
        for method_name, method in protocol_type.__dict__.items():
            if method_name.startswith("_") or not inspect.isfunction(method):
                continue
            assert _signature_text(AppShellRepositoryStore, method_name) == (
                _signature_text(protocol_type, method_name)
            )


def test_runtime_store_protocol_type_hints_are_resolvable():
    from models.agent_group import AgentGroup
    from models.room import RoomAgentMessage

    agent_group_hints = get_type_hints(RuntimeAgentRoomStore.add_agent_group)
    task_hints = get_type_hints(
        RuntimeTaskLifecycleStore.resolve_client_request_id_for_agent_message
    )

    assert agent_group_hints["agent_group"] is AgentGroup
    assert task_hints["room_agent_message"] is RoomAgentMessage


def test_protocol_import_tolerates_json_log_format_environment():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from common.protocols import RuntimeAgentRoomStore, "
                "APIKeyAuthenticator; "
                "print(RuntimeAgentRoomStore.__name__, APIKeyAuthenticator.__name__)"
            ),
        ],
        check=False,
        capture_output=True,
        env={"LOG_FORMAT": "json", "LOG_PATH": "/tmp/hybro-protocol-import.log"},
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "RuntimeAgentRoomStore APIKeyAuthenticator" in result.stdout
