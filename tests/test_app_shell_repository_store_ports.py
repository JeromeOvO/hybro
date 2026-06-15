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


class _FakeMongo:
    def __init__(self) -> None:
        self.collections: dict[str, object] = {}

    def collection(self, name: str) -> object:
        return self.collections.setdefault(name, object())


def _make_app_shell_store():
    from app_shell.repository_store import AppShellRepositoryStore

    return AppShellRepositoryStore(
        mongo=_FakeMongo(),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )


def test_app_shell_repository_store_wires_agent_room_part():
    from app_shell.repository_parts.agent_room_store import AppShellAgentRoomStore

    store = _make_app_shell_store()

    assert isinstance(store.agent_room, AppShellAgentRoomStore)


def test_app_shell_repository_store_wires_message_part():
    from app_shell.repository_parts.message_store import AppShellMessageStore

    store = _make_app_shell_store()

    assert isinstance(store.messages, AppShellMessageStore)


def test_app_shell_repository_store_wires_task_lifecycle_part():
    from app_shell.repository_parts.task_lifecycle_store import (
        AppShellTaskLifecycleStore,
    )

    store = _make_app_shell_store()

    assert isinstance(store.tasks, AppShellTaskLifecycleStore)


def test_app_shell_repository_store_wires_hitl_part():
    from app_shell.repository_parts.hitl_store import AppShellHITLStore

    store = _make_app_shell_store()

    assert isinstance(store.hitl, AppShellHITLStore)


def test_app_shell_repository_store_wires_memory_part():
    from app_shell.repository_parts.memory_store import AppShellMemoryStore

    store = _make_app_shell_store()

    assert isinstance(store.memory, AppShellMemoryStore)


def test_app_shell_repository_store_wires_all_focused_parts():
    from app_shell.repository_parts import (
        AppShellAgentRoomStore,
        AppShellHITLStore,
        AppShellMemoryStore,
        AppShellMessageStore,
        AppShellTaskLifecycleStore,
    )

    store = _make_app_shell_store()

    assert isinstance(store.agent_room, AppShellAgentRoomStore)
    assert isinstance(store.messages, AppShellMessageStore)
    assert isinstance(store.tasks, AppShellTaskLifecycleStore)
    assert isinstance(store.hitl, AppShellHITLStore)
    assert isinstance(store.memory, AppShellMemoryStore)


def test_app_shell_repository_store_part_properties_do_not_recreate_missing_parts():
    store = _make_app_shell_store()

    del store._agent_room_part
    del store._message_part
    del store._task_lifecycle_part
    del store._hitl_part
    del store._memory_part

    for attribute in ("agent_room", "messages", "tasks", "hitl", "memory"):
        try:
            getattr(store, attribute)
        except AttributeError:
            continue
        raise AssertionError(f"{attribute} should expose missing store wiring")
